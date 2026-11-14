"""
Comprehensive tests for VoxPilot Phase 2A: Personalization Foundation.

Covers:
1. SQLModel PreferenceRecord & idempotent migration
2. PersonalizationService CRUD, validation, secret blocking, duplicates, relevance filtering
3. VoxPilotAgent integration (personalization_context passed to OllamaLLM, disabled exclusion, offline fallback)
4. REST API (/api/personalization & /agent/personalization) + CSRF origin enforcement
5. Playwright UI tests for PersonalizationCard in SettingsPage
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from sqlmodel import create_engine

from backend.agent.graph import VoxPilotAgent
from backend.agent.intent import IntentRecognizer
from backend.agent.llm import LLMError, OllamaLLM
from backend.agent.planner import AgentPlanner
from backend.database.connection import run_migrations_sync
from backend.database.models import VALID_PREFERENCE_CATEGORIES
from backend.database.repository import DatabaseRepository
from backend.main import app
from backend.memory.context_manager import ConversationContextManager
from backend.personalization import (
    DuplicatePreferenceError,
    InvalidPreferenceCategoryError,
    InvalidPreferenceValueError,
    PersonalizationService,
    PreferencePrivacyError,
    reset_shared_personalization_service,
)
from tests.test_frontend_browser import (
    FRONTEND_DIST,
    StaticSpaHandler,
    setup_default_mock_routes,
)


@pytest.fixture()
def isolated_engine(tmp_path: Path):
    db_file = tmp_path / "phase2a_test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    run_migrations_sync(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def personalization_service(isolated_engine):
    return PersonalizationService(sync_engine=isolated_engine)


# ==============================================================================
# 1. Database & Migration Tests
# ==============================================================================

def test_preference_migration_is_idempotent(isolated_engine):
    result = run_migrations_sync(isolated_engine)
    assert "ensured_preferencerecord_index" in result["applied_steps"]

    with isolated_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='preferencerecord'")
        ).fetchall()
        assert len(rows) == 1

        idx_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_preferencerecord_profile_cat_key'")
        ).fetchall()
        assert len(idx_rows) == 1


# ==============================================================================
# 2. PersonalizationService Tests
# ==============================================================================

def test_service_crud_and_duplicate_prevention(personalization_service: PersonalizationService):
    created = personalization_service.create_preference(
        category="communication",
        key="Response Style",
        value="Concise bullet points",
        description="Prefer short structured answers",
        priority=5,
    )
    assert created["id"].startswith("pref_")
    assert created["normalized_key"] == "response_style"
    assert created["enabled"] is True
    assert created["priority"] == 5

    # Case/whitespace/hyphen-insensitive duplicate blocked by default
    with pytest.raises(DuplicatePreferenceError):
        personalization_service.create_preference(
            category="communication",
            key="response-style",
            value="Verbose",
        )

    # Upsert updates existing record
    upserted = personalization_service.create_preference(
        category="communication",
        key="response_style",
        value="Direct and concise",
        upsert=True,
    )
    assert upserted["id"] == created["id"]
    assert upserted["value"] == "Direct and concise"

    fetched = personalization_service.get_preference(created["id"])
    assert fetched is not None
    assert fetched["value"] == "Direct and concise"

    updated = personalization_service.update_preference(
        created["id"],
        enabled=False,
        priority=4,
    )
    assert updated is not None
    assert updated["enabled"] is False
    assert updated["priority"] == 4

    assert personalization_service.count_preferences(enabled_only=True) == 0
    assert personalization_service.count_preferences(enabled_only=False) == 1

    assert personalization_service.delete_preference(created["id"]) is True
    assert personalization_service.get_preference(created["id"]) is None


def test_service_validation_and_secret_rejection(personalization_service: PersonalizationService):
    with pytest.raises(InvalidPreferenceCategoryError):
        personalization_service.create_preference(
            category="invalid_cat",
            key="tone",
            value="friendly",
        )

    with pytest.raises(InvalidPreferenceValueError):
        personalization_service.create_preference(
            category="communication",
            key="   ",
            value="friendly",
        )

    with pytest.raises(InvalidPreferenceValueError):
        personalization_service.create_preference(
            category="communication",
            key="tone",
            value="friendly",
            priority=9,
        )

    for secret_value in (
        "sk-1234567890abcdefghijklmnop",
        "ghp_1234567890abcdefghijklmnopqrstuv",
        "password=SuperSecretPassword123!",
    ):
        with pytest.raises(PreferencePrivacyError):
            personalization_service.create_preference(
                category="technical",
                key="api_secret",
                value=secret_value,
            )


def test_build_personalization_context_relevance_and_disabled_exclusion(
    personalization_service: PersonalizationService,
):
    personalization_service.create_preference(
        category="communication",
        key="tone",
        value="Direct and concise",
        enabled=True,
        priority=5,
    )
    personalization_service.create_preference(
        category="response_format",
        key="disabled_rule",
        value="Always output XML",
        enabled=False,
        priority=5,
    )
    personalization_service.create_preference(
        category="technical",
        key="python_version",
        value="Use Python 3.12 type hints",
        enabled=True,
        priority=4,
    )
    personalization_service.create_preference(
        category="interface",
        key="dashboard_density",
        value="Compact tables",
        enabled=True,
        priority=3,
    )

    # Unrelated casual prompt: core communication included, technical/interface excluded, disabled excluded
    ctx_hello = personalization_service.build_personalization_context(user_input="Hello there!")
    assert "Direct and concise" in ctx_hello["summary_text"]
    assert "Always output XML" not in ctx_hello["summary_text"]
    assert "Python 3.12" not in ctx_hello["summary_text"]
    assert "Compact tables" not in ctx_hello["summary_text"]

    # Coding prompt: core communication + relevant technical preference included
    ctx_python = personalization_service.build_personalization_context(
        user_input="Help me debug this python script"
    )
    assert "Direct and concise" in ctx_python["summary_text"]
    assert "Python 3.12" in ctx_python["summary_text"]
    assert "Compact tables" not in ctx_python["summary_text"]


# ==============================================================================
# 3. VoxPilotAgent Integration Tests
# ==============================================================================

def test_agent_injects_active_preferences_and_preserves_memory(isolated_engine):
    repo = DatabaseRepository(sync_engine=isolated_engine)
    pers_svc = PersonalizationService(sync_engine=repo._sync_engine)
    pers_svc.create_preference(
        category="communication",
        key="style",
        value="Reply in two concise bullet points",
        enabled=True,
        priority=5,
    )
    pers_svc.create_preference(
        category="ai_behavior",
        key="disabled_pref",
        value="Never use lists",
        enabled=False,
        priority=5,
    )

    fake_intent = MagicMock(spec=IntentRecognizer)
    fake_intent.recognize_intent.return_value = {
        "intent": "general_conversation",
        "confidence": 0.96,
        "entities": {},
        "reason": "conversational",
        "raw_input": "How should I organize my day?",
    }
    fake_planner = MagicMock(spec=AgentPlanner)
    fake_planner.create_plan.return_value = {
        "plan": [{"step": 1, "action": "respond", "arguments": {"message": "Generate conversational response"}}],
        "requires_confirmation": False,
    }

    captured_calls = []

    class CapturingLLM(OllamaLLM):
        def generate_contextual_response(
            self,
            user_message,
            conversation_history=None,
            recent_turns_text=None,
            memories=None,
            personalization_context=None,
        ):
            captured_calls.append(
                {
                    "user_message": user_message,
                    "personalization_context": personalization_context,
                    "memories": memories,
                }
            )
            return "- Prioritize top 2 tasks.\n- Block focus time."

    agent = VoxPilotAgent(
        intent_recognizer=fake_intent,
        planner=fake_planner,
        context_manager=ConversationContextManager(),
        repository=repo,
        llm=CapturingLLM(model="qwen3:4b"),
        personalization_service=pers_svc,
    )
    agent.memory_service.create_memory(
        content="User wants to organize daily focus tasks.",
        memory_type="user_fact",
        importance=4,
    )

    result = agent.process_request(
        "How should I organize my day?",
        conversation_id="conv_pers_1",
    )
    assert result["execution_status"] == "completed"
    assert "Prioritize top 2 tasks" in result["response"]
    assert len(captured_calls) == 1
    p_ctx = captured_calls[0]["personalization_context"] or ""
    assert "Reply in two concise bullet points" in p_ctx
    assert "Never use lists" not in p_ctx
    # Retrieved memories still passed alongside personalization
    assert len(captured_calls[0]["memories"] or []) >= 1


def test_agent_offline_fallback_with_personalization(isolated_engine):
    repo = DatabaseRepository(sync_engine=isolated_engine)
    pers_svc = PersonalizationService(sync_engine=repo._sync_engine)
    pers_svc.create_preference(
        category="communication",
        key="tone",
        value="Concise",
        enabled=True,
    )

    fake_intent = MagicMock(spec=IntentRecognizer)
    fake_intent.recognize_intent.return_value = {
        "intent": "general_conversation",
        "confidence": 0.9,
        "entities": {},
        "reason": "conversational",
        "raw_input": "Hi",
    }
    fake_planner = MagicMock(spec=AgentPlanner)
    fake_planner.create_plan.return_value = {
        "plan": [{"step": 1, "action": "respond", "arguments": {"message": "Generate conversational response"}}],
        "requires_confirmation": False,
    }

    class OfflineLLM(OllamaLLM):
        def generate_contextual_response(self, *args, **kwargs):
            raise LLMError("Ollama offline")

    agent = VoxPilotAgent(
        intent_recognizer=fake_intent,
        planner=fake_planner,
        context_manager=ConversationContextManager(),
        repository=repo,
        llm=OfflineLLM(model="qwen3:4b"),
        personalization_service=pers_svc,
    )
    result = agent.process_request("Hi", conversation_id="conv_pers_offline")
    assert result["execution_status"] == "completed"
    assert result["response"]


# ==============================================================================
# 4. REST API Tests (/api/personalization & /agent/personalization)
# ==============================================================================

def test_personalization_api_crud_and_origin_security(monkeypatch, isolated_engine):
    reset_shared_personalization_service()
    svc = PersonalizationService(sync_engine=isolated_engine)
    monkeypatch.setattr(
        "backend.api.personalization.get_shared_personalization_service",
        lambda: svc,
    )

    client = TestClient(app)

    for base in ("/api/personalization", "/agent/personalization"):
        # List initially empty
        r_list = client.get(base)
        assert r_list.status_code == 200
        body = r_list.json()
        assert body["categories"] == list(VALID_PREFERENCE_CATEGORIES)

    # Create via /api/personalization
    r_create = client.post(
        "/api/personalization",
        json={
            "category": "response_format",
            "key": "code_blocks",
            "value": "Always include language tags",
            "priority": 4,
        },
        headers={"Origin": "http://localhost:5173"},
    )
    assert r_create.status_code == 201
    created = r_create.json()
    pref_id = created["id"]

    # Duplicate rejected with 409
    r_dup = client.post(
        "/api/personalization",
        json={
            "category": "response_format",
            "key": "Code Blocks",
            "value": "Something else",
        },
    )
    assert r_dup.status_code == 409

    # Secret rejected with 400
    r_secret = client.post(
        "/api/personalization",
        json={
            "category": "technical",
            "key": "token",
            "value": "sk-1234567890abcdefghijklmnop",
        },
    )
    assert r_secret.status_code == 400

    # Untrusted Origin rejected with 403
    r_csrf = client.post(
        "/api/personalization",
        json={"category": "communication", "key": "tone", "value": "warm"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r_csrf.status_code == 403

    # Read & Update via /agent/personalization
    r_get = client.get(f"/agent/personalization/{pref_id}")
    assert r_get.status_code == 200
    assert r_get.json()["value"] == "Always include language tags"

    r_patch = client.patch(
        f"/agent/personalization/{pref_id}",
        json={"enabled": False, "value": "Use fenced TypeScript blocks"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["enabled"] is False
    assert r_patch.json()["value"] == "Use fenced TypeScript blocks"

    # Delete
    r_del = client.delete(f"/api/personalization/{pref_id}")
    assert r_del.status_code == 200
    assert r_del.json()["deleted"] is True

    r_missing = client.get(f"/api/personalization/{pref_id}")
    assert r_missing.status_code == 404


# ==============================================================================
# 5. Playwright UI Tests for PersonalizationCard
# ==============================================================================

@pytest.fixture(scope="module")
def ui_static_server():
    if not FRONTEND_DIST.exists() or not (FRONTEND_DIST / "index.html").exists():
        pytest.skip("Frontend dist missing. Run 'npm run build' first.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StaticSpaHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_settings_personalization_ui_crud_flow(ui_static_server: str):
    store = [
        {
            "id": "pref_initial_1",
            "preference_id": "pref_initial_1",
            "profile_id": "default",
            "category": "communication",
            "key": "response_style",
            "normalized_key": "response_style",
            "value": "Concise bullet points",
            "description": "Default style",
            "enabled": True,
            "priority": 4,
            "created_at": "2026-11-14T10:00:00+00:00",
            "updated_at": "2026-11-14T10:00:00+00:00",
            "metadata": {},
        }
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        setup_default_mock_routes(page)

        def handle_personalization(route):
            req = route.request
            method = req.method
            url = req.url

            if method == "GET" and "/api/personalization" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "preferences": store,
                            "total": len(store),
                            "limit": 100,
                            "offset": 0,
                            "categories": list(VALID_PREFERENCE_CATEGORIES),
                        }
                    ),
                )
                return

            if method == "POST" and url.endswith("/api/personalization"):
                payload = json.loads(req.post_data or "{}")
                new_item = {
                    "id": "pref_ui_new_2",
                    "preference_id": "pref_ui_new_2",
                    "profile_id": "default",
                    "category": payload.get("category", "technical"),
                    "key": payload["key"],
                    "normalized_key": payload["key"].lower().replace(" ", "_"),
                    "value": payload["value"],
                    "description": payload.get("description"),
                    "enabled": payload.get("enabled", True),
                    "priority": payload.get("priority", 3),
                    "created_at": "2026-11-14T10:05:00+00:00",
                    "updated_at": "2026-11-14T10:05:00+00:00",
                    "metadata": {},
                }
                store.append(new_item)
                route.fulfill(status=201, content_type="application/json", body=json.dumps(new_item))
                return

            if method == "PATCH" and "/api/personalization/" in url:
                pref_id = url.rsplit("/", 1)[-1]
                payload = json.loads(req.post_data or "{}")
                for item in store:
                    if item["id"] == pref_id:
                        item.update(payload)
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(item))
                        return
                route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "Not found"}))
                return

            if method == "DELETE" and "/api/personalization/" in url:
                pref_id = url.rsplit("/", 1)[-1]
                store[:] = [item for item in store if item["id"] != pref_id]
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"deleted": True, "preference_id": pref_id}),
                )
                return

            route.fallback_()

        page.route("**/api/personalization**", handle_personalization)

        page.goto(ui_static_server)
        page.wait_for_selector("button.nav-item:has-text('Settings')")
        page.locator("button.nav-item:has-text('Settings')").click()
        card = page.get_by_test_id("personalization-card")
        card.wait_for(state="visible", timeout=10000)

        # Initial preference visible
        assert "Concise bullet points" in card.inner_text()

        # Add preference
        page.get_by_test_id("add-preference-btn").click()
        page.get_by_label("Preference Key").fill("preferred_language")
        page.get_by_label("Preference Value").fill("Python 3.12")
        page.get_by_test_id("save-preference-btn").click()

        page.get_by_text("Python 3.12").wait_for(state="visible", timeout=5000)

        # Toggle disable on initial preference
        page.get_by_label("Disable preference response_style").click()
        page.get_by_label("Enable preference response_style").wait_for(state="visible", timeout=5000)

        # Two-step delete on newly added preference
        page.get_by_label("Delete preference preferred_language").click()
        page.get_by_role("button", name="Confirm Delete").click()
        page.get_by_text("Preference deleted from local storage.").wait_for(state="visible", timeout=5000)

        browser.close()
