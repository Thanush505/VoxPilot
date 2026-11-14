"""
Comprehensive tests for VoxPilot Phase 2B: Multi-Profile Support.

Covers:
1. Database & idempotent legacy-data migration to default profile
2. ProfileService CRUD, validation, duplicate/secret/default-deletion protection
3. Strict backend isolation for memories, personalization, conversations, executions & hydration
4. VoxPilotAgent active-profile resolution, context isolation & execution resumption guard
5. REST API (/api/profiles, /agent/profiles, active-profile scoping on memory & personalization)
6. Playwright UI tests for Header profile switcher & ProfileManagementCard
"""

from __future__ import annotations

import http.server
import json
import sqlite3
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
from backend.database.repository import DatabaseRepository
from backend.main import app
from backend.memory.context_manager import ConversationContextManager
from backend.memory.conversation_hydrator import ConversationHydrator
from backend.memory.long_term_memory import (
    LongTermMemoryService,
    reset_shared_long_term_memory_service,
)
from backend.personalization import (
    PersonalizationService,
    reset_shared_personalization_service,
)
from backend.profiles import (
    DefaultProfileDeletionError,
    DuplicateProfileError,
    InvalidProfileError,
    ProfilePrivacyError,
    ProfileService,
    reset_shared_profile_service,
)
from tests.test_frontend_browser import (
    FRONTEND_DIST,
    StaticSpaHandler,
    setup_default_mock_routes,
)


@pytest.fixture()
def isolated_engine(tmp_path: Path):
    db_file = tmp_path / "phase2b_test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    run_migrations_sync(engine)
    yield engine
    engine.dispose()


# ==============================================================================
# 1. Database & Legacy Migration Tests
# ==============================================================================

def test_default_profile_created_and_legacy_data_migrated_idempotently(tmp_path: Path):
    legacy_db = tmp_path / "legacy_pre2b.db"
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE conversationrecord (
            conversation_id TEXT PRIMARY KEY,
            profile_id TEXT,
            title TEXT,
            summary TEXT,
            active_context_json TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE executionrecord (
            execution_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            user_input TEXT NOT NULL,
            intent TEXT,
            execution_status TEXT NOT NULL,
            plan_json TEXT,
            current_step INTEGER NOT NULL DEFAULT 0,
            response TEXT,
            error TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            completed_at DATETIME
        );
        CREATE TABLE memoryrecord (
            id TEXT PRIMARY KEY,
            profile_id TEXT,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            source_conversation_id TEXT,
            importance INTEGER NOT NULL DEFAULT 3,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            last_accessed_at DATETIME,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE preferencerecord (
            id TEXT PRIMARY KEY,
            profile_id TEXT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            value TEXT NOT NULL,
            description TEXT,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 3,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        INSERT INTO conversationrecord (conversation_id, profile_id, title, created_at, updated_at)
        VALUES ('conv_old_1', NULL, 'Old Conv', '2026-10-01T10:00:00', '2026-10-01T10:05:00');

        INSERT INTO executionrecord (execution_id, conversation_id, user_input, execution_status, created_at, updated_at)
        VALUES ('exec_old_1', 'conv_old_1', 'Open Notepad', 'completed', '2026-10-01T10:00:00', '2026-10-01T10:01:00');

        INSERT INTO memoryrecord (id, profile_id, memory_type, content, normalized_content, created_at, updated_at)
        VALUES ('mem_old_1', NULL, 'user_fact', 'Legacy memory fact', 'legacy memory fact', '2026-10-01T10:00:00', '2026-10-01T10:00:00');

        INSERT INTO preferencerecord (id, profile_id, category, key, normalized_key, value, created_at, updated_at)
        VALUES ('pref_old_1', '', 'communication', 'tone', 'tone', 'Concise', '2026-10-01T10:00:00', '2026-10-01T10:00:00');
        """
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{legacy_db}", connect_args={"check_same_thread": False})
    try:
        res1 = run_migrations_sync(engine)
        assert "created_default_profile" in res1["applied_steps"]
        assert "activated_default_profile" in res1["applied_steps"]

        # Idempotent second run
        res2 = run_migrations_sync(engine)
        assert "created_default_profile" not in res2["applied_steps"]

        with engine.connect() as db_conn:
            prof = db_conn.execute(
                text("SELECT id, is_default, is_active FROM profilerecord WHERE id = 'default'")
            ).fetchone()
            assert prof is not None and prof[1] == 1 and prof[2] == 1

            conv = db_conn.execute(
                text("SELECT profile_id FROM conversationrecord WHERE conversation_id = 'conv_old_1'")
            ).fetchone()
            assert conv[0] == "default"

            ex = db_conn.execute(
                text("SELECT profile_id FROM executionrecord WHERE execution_id = 'exec_old_1'")
            ).fetchone()
            assert ex[0] == "default"

            mem = db_conn.execute(
                text("SELECT profile_id FROM memoryrecord WHERE id = 'mem_old_1'")
            ).fetchone()
            assert mem[0] == "default"

            pref = db_conn.execute(
                text("SELECT profile_id FROM preferencerecord WHERE id = 'pref_old_1'")
            ).fetchone()
            assert pref[0] == "default"
    finally:
        engine.dispose()


# ==============================================================================
# 2. ProfileService Tests
# ==============================================================================

def test_profile_service_crud_activation_and_protections(isolated_engine):
    svc = ProfileService(sync_engine=isolated_engine)

    profiles = svc.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["id"] == "default"
    assert profiles[0]["is_default"] is True
    assert profiles[0]["is_active"] is True

    # Create & activate Work profile
    work = svc.create_profile(
        name="Work",
        description="Engineering tasks",
        avatar="briefcase",
        activate=True,
    )
    assert work["name"] == "Work"
    assert work["is_active"] is True
    assert svc.get_active_profile_id() == work["id"]

    # Duplicate name blocked (case/whitespace insensitive)
    with pytest.raises(DuplicateProfileError):
        svc.create_profile(name="  work  ")

    # Secret and invalid inputs blocked
    with pytest.raises(ProfilePrivacyError):
        svc.create_profile(name="sk-1234567890abcdefghijklmnop")
    with pytest.raises(InvalidProfileError):
        svc.create_profile(name="   ")
    with pytest.raises(InvalidProfileError):
        svc.create_profile(name="College", avatar="invalid-avatar")

    # Update Work profile
    updated = svc.update_profile(work["id"], name="Work Dev", avatar="code")
    assert updated is not None
    assert updated["name"] == "Work Dev"
    assert updated["avatar"] == "code"

    # Cannot delete default profile
    with pytest.raises(DefaultProfileDeletionError):
        svc.delete_profile("default")

    # Deleting currently active profile safely reverts active profile to default
    assert svc.delete_profile(work["id"]) is True
    assert svc.get_profile(work["id"]) is None
    assert svc.get_active_profile_id() == "default"


# ==============================================================================
# 3. Strict Profile Data Isolation Tests
# ==============================================================================

def test_strict_isolation_across_memories_preferences_conversations_and_hydration(isolated_engine):
    prof_svc = ProfileService(sync_engine=isolated_engine)
    prof_a = prof_svc.create_profile(name="Personal", avatar="user")
    prof_b = prof_svc.create_profile(name="Work", avatar="briefcase")
    pid_a, pid_b = prof_a["id"], prof_b["id"]

    mem_svc = LongTermMemoryService(sync_engine=isolated_engine)
    pers_svc = PersonalizationService(sync_engine=isolated_engine)
    repo = DatabaseRepository(sync_engine=isolated_engine)
    hydrator = ConversationHydrator(repository=repo)

    # Memories
    mem_a = mem_svc.create_memory(
        content="Personal secret hobby is astrophotography.",
        memory_type="user_fact",
        profile_id=pid_a,
    )
    mem_b = mem_svc.create_memory(
        content="Work project codename is Orion.",
        memory_type="project_context",
        profile_id=pid_b,
    )

    assert len(mem_svc.list_memories(profile_id=pid_a)["memories"]) == 1
    assert mem_svc.search_memories("Orion", profile_id=pid_a) == []
    assert mem_svc.get_memory(mem_b["id"], profile_id=pid_a) is None
    assert mem_svc.update_memory(mem_b["id"], content="Tampered", profile_id=pid_a) is None
    assert mem_svc.delete_memory(mem_b["id"], profile_id=pid_a) is False
    assert mem_svc.get_memory(mem_b["id"], profile_id=pid_b) is not None
    assert mem_svc.get_memory(mem_a["id"], profile_id=pid_a) is not None

    # Personalization
    pref_a = pers_svc.create_preference(
        category="communication",
        key="tone",
        value="Casual and warm",
        profile_id=pid_a,
    )
    pref_b = pers_svc.create_preference(
        category="communication",
        key="tone",
        value="Formal executive brief",
        profile_id=pid_b,
    )

    assert pers_svc.get_preference(pref_b["id"], profile_id=pid_a) is None
    assert pers_svc.update_preference(pref_b["id"], value="Hacked", profile_id=pid_a) is None
    assert pers_svc.delete_preference(pref_b["id"], profile_id=pid_a) is False

    ctx_a = pers_svc.build_personalization_context(user_input="Hello", profile_id=pid_a)
    ctx_b = pers_svc.build_personalization_context(user_input="Hello", profile_id=pid_b)
    assert "Casual and warm" in ctx_a["summary_text"]
    assert "Formal executive brief" not in ctx_a["summary_text"]
    assert "Formal executive brief" in ctx_b["summary_text"]
    assert "Casual and warm" not in ctx_b["summary_text"]
    assert pref_a["id"] != pref_b["id"]

    # Conversations, Executions & Hydration
    repo.save_execution_sync(
        {
            "execution_id": "exec_b_1",
            "conversation_id": "conv_b_1",
            "profile_id": pid_b,
            "user_input": "Work confidential query",
            "execution_status": "completed",
            "plan": [],
            "response": "Work confidential reply",
        }
    )
    hydrator.save_context("conv_b_1", ConversationContextManager().get_context("conv_b_1"), profile_id=pid_b)

    assert repo.get_conversation_sync("conv_b_1", profile_id=pid_a) is None
    assert repo.get_execution_sync("exec_b_1", profile_id=pid_a) is None
    assert repo.list_executions_for_conversation_sync("conv_b_1", profile_id=pid_a) == []

    # Hydrator under Profile A must NOT load Profile B's execution turns
    cm_a = ConversationContextManager()
    ctx_hydrated_a = hydrator.hydrate_context("conv_b_1", cm_a, profile_id=pid_a)
    assert ctx_hydrated_a.turns == []

    # Hydrator under Profile B DOES load Profile B's execution turns
    cm_b = ConversationContextManager()
    ctx_hydrated_b = hydrator.hydrate_context("conv_b_1", cm_b, profile_id=pid_b)
    assert len(ctx_hydrated_b.turns) == 1
    assert "Work confidential query" in ctx_hydrated_b.turns[0].user_input


# ==============================================================================
# 4. VoxPilotAgent Integration & Resumption Guard Tests
# ==============================================================================

def test_agent_profile_switching_isolation_and_resumption_guard(isolated_engine):
    repo = DatabaseRepository(sync_engine=isolated_engine)
    prof_svc = ProfileService(sync_engine=isolated_engine)
    prof_a = prof_svc.create_profile(name="Personal", activate=True)
    prof_b = prof_svc.create_profile(name="Work", activate=False)

    mem_svc = LongTermMemoryService(sync_engine=isolated_engine)
    mem_svc.create_memory(content="Personal focus is marathon training.", profile_id=prof_a["id"])
    mem_svc.create_memory(content="Work focus is quarterly audit.", profile_id=prof_b["id"])

    pers_svc = PersonalizationService(sync_engine=isolated_engine)
    pers_svc.create_preference(
        category="communication",
        key="style",
        value="Friendly personal coach",
        profile_id=prof_a["id"],
    )
    pers_svc.create_preference(
        category="communication",
        key="style",
        value="Strict corporate bullet points",
        profile_id=prof_b["id"],
    )

    fake_intent = MagicMock(spec=IntentRecognizer)
    fake_intent.recognize_intent.return_value = {
        "intent": "general_conversation",
        "confidence": 0.95,
        "entities": {},
        "reason": "conversational",
        "raw_input": "What is my focus?",
    }
    fake_planner = MagicMock(spec=AgentPlanner)
    fake_planner.create_plan.return_value = {
        "plan": [{"step": 1, "action": "respond", "arguments": {"message": "Generate conversational response"}}],
        "requires_confirmation": False,
    }

    captured = []

    class CapturingLLM(OllamaLLM):
        def generate_contextual_response(
            self,
            user_message,
            conversation_history=None,
            recent_turns_text=None,
            memories=None,
            personalization_context=None,
        ):
            captured.append(
                {
                    "memories": memories or [],
                    "personalization_context": personalization_context or "",
                }
            )
            return "Response OK"

    agent = VoxPilotAgent(
        intent_recognizer=fake_intent,
        planner=fake_planner,
        context_manager=ConversationContextManager(),
        repository=repo,
        llm=CapturingLLM(model="qwen3:4b"),
        memory_service=mem_svc,
        personalization_service=pers_svc,
        profile_service=prof_svc,
    )

    # Request under Profile A
    res_a = agent.process_request("What is my focus?", conversation_id="conv_a_only")
    assert res_a["profile_id"] == prof_a["id"]
    assert "Friendly personal coach" in captured[0]["personalization_context"]
    assert "Strict corporate" not in captured[0]["personalization_context"]
    assert any("marathon" in m["content"] for m in captured[0]["memories"])
    assert not any("quarterly audit" in m["content"] for m in captured[0]["memories"])

    # Switch active profile to B
    prof_svc.activate_profile(prof_b["id"])

    # Attempting to reuse Profile A's conversation_id under Profile B must be blocked
    with pytest.raises(ValueError, match="belongs to another profile"):
        agent.process_request("Continue conv A", conversation_id="conv_a_only")

    # Request under Profile B with its own conversation_id
    res_b = agent.process_request("What is my focus?", conversation_id="conv_b_only")
    assert res_b["profile_id"] == prof_b["id"]
    assert "Strict corporate bullet points" in captured[1]["personalization_context"]
    assert "Friendly personal coach" not in captured[1]["personalization_context"]
    assert any("quarterly audit" in m["content"] for m in captured[1]["memories"])
    assert not any("marathon" in m["content"] for m in captured[1]["memories"])

    # Execution resumption guard: foreign conversation_id rejected
    with pytest.raises(ValueError, match="belongs to another profile"):
        agent.resume_execution(
            {
                "execution_id": "exec_fake",
                "conversation_id": "conv_a_only",
                "profile_id": prof_b["id"],
                "execution_status": "confirmation_required",
                "plan": [{"step": 1, "action": "respond", "parameters": {}}],
                "current_step": 0,
            },
            confirmation_token="tok_123",
        )


def test_agent_offline_fallback_with_active_profile(isolated_engine):
    repo = DatabaseRepository(sync_engine=isolated_engine)
    prof_svc = ProfileService(sync_engine=isolated_engine)
    prof = prof_svc.create_profile(name="OfflineWork", activate=True)

    fake_intent = MagicMock(spec=IntentRecognizer)
    fake_intent.recognize_intent.return_value = {
        "intent": "general_conversation",
        "confidence": 0.9,
        "entities": {},
        "reason": "conversational",
        "raw_input": "Hello",
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
        profile_service=prof_svc,
    )
    res = agent.process_request("Hello", conversation_id="conv_offline_prof")
    assert res["execution_status"] == "completed"
    assert res["profile_id"] == prof["id"]


# ==============================================================================
# 5. Profile REST API & Active-Profile Scoping Tests
# ==============================================================================

def test_profile_api_endpoints_and_active_scoping(monkeypatch, isolated_engine):
    reset_shared_profile_service()
    reset_shared_long_term_memory_service()
    reset_shared_personalization_service()

    prof_svc = ProfileService(sync_engine=isolated_engine)
    mem_svc = LongTermMemoryService(sync_engine=isolated_engine)
    pers_svc = PersonalizationService(sync_engine=isolated_engine)

    monkeypatch.setattr("backend.api.profiles.get_shared_profile_service", lambda: prof_svc)
    monkeypatch.setattr("backend.api.memory.get_shared_profile_service", lambda: prof_svc)
    monkeypatch.setattr("backend.api.personalization.get_shared_profile_service", lambda: prof_svc)
    monkeypatch.setattr("backend.api.memory.get_shared_long_term_memory_service", lambda: mem_svc)
    monkeypatch.setattr("backend.api.personalization.get_shared_personalization_service", lambda: pers_svc)

    client = TestClient(app)

    # List profiles on both prefixes
    for base in ("/api/profiles", "/agent/profiles"):
        r = client.get(base)
        assert r.status_code == 200
        assert r.json()["active_profile"]["id"] == "default"

    # Create College profile & activate it
    r_create = client.post(
        "/api/profiles",
        json={"name": "College", "description": "Study profile", "avatar": "graduation-cap", "activate": True},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r_create.status_code == 201
    college_id = r_create.json()["id"]

    # Active endpoint reflects College
    assert client.get("/api/profiles/active").json()["id"] == college_id

    # Creating memory & preference without explicit profile_id automatically scopes to College
    r_mem = client.post(
        "/api/memory",
        json={"content": "Studying linear algebra this semester.", "memory_type": "user_fact"},
    )
    assert r_mem.status_code == 201
    assert r_mem.json()["profile_id"] == college_id
    college_mem_id = r_mem.json()["id"]

    r_pref = client.post(
        "/api/personalization",
        json={"category": "communication", "key": "depth", "value": "Explain step by step"},
    )
    assert r_pref.status_code == 201
    assert r_pref.json()["profile_id"] == college_id
    college_pref_id = r_pref.json()["id"]

    # Switch back to default via /activate
    r_act = client.post(f"/agent/profiles/default/activate")
    assert r_act.status_code == 200
    assert r_act.json()["id"] == "default"

    # Under default, College's memory & preference are NOT visible or deletable
    assert client.get("/api/memory").json()["total"] == 0
    assert client.get(f"/api/memory/{college_mem_id}").status_code == 404
    assert client.delete(f"/api/memory/{college_mem_id}").status_code == 404
    assert client.get("/api/personalization").json()["total"] == 0
    assert client.get(f"/api/personalization/{college_pref_id}").status_code == 404

    # Default profile deletion rejected with 400
    assert client.delete("/api/profiles/default").status_code == 400

    # Untrusted origin rejected with 403
    assert client.post(
        "/api/profiles",
        json={"name": "Untrusted"},
        headers={"Origin": "https://evil.example.com"},
    ).status_code == 403

    # Eligible profile deletion succeeds
    r_del = client.delete(f"/api/profiles/{college_id}")
    assert r_del.status_code == 200
    assert r_del.json()["deleted"] is True


# ==============================================================================
# 6. Playwright UI Tests for Profile Switcher & ProfileManagementCard
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


def test_profile_switcher_and_management_ui_flow(ui_static_server: str):
    profiles_store = [
        {
            "id": "default",
            "profile_id": "default",
            "name": "Default",
            "normalized_name": "default",
            "description": "Default local VoxPilot profile",
            "avatar": "user",
            "is_active": True,
            "is_default": True,
            "created_at": "2026-11-15T00:00:00+00:00",
            "updated_at": "2026-11-15T00:00:00+00:00",
        }
    ]

    def active_profile():
        for p in profiles_store:
            if p["is_active"]:
                return p
        return profiles_store[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        setup_default_mock_routes(page)

        def handle_profiles(route):
            req = route.request
            method = req.method
            url = req.url

            if method == "GET" and url.endswith("/api/profiles"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "profiles": profiles_store,
                            "active_profile": active_profile(),
                            "total": len(profiles_store),
                            "avatars": ["user", "briefcase", "graduation-cap", "code", "home", "sparkles"],
                        }
                    ),
                )
                return

            if method == "POST" and url.endswith("/api/profiles"):
                payload = json.loads(req.post_data or "{}")
                if payload.get("activate"):
                    for prof in profiles_store:
                        prof["is_active"] = False
                new_prof = {
                    "id": "prof_work_1",
                    "profile_id": "prof_work_1",
                    "name": payload["name"],
                    "normalized_name": payload["name"].strip().lower(),
                    "description": payload.get("description"),
                    "avatar": payload.get("avatar", "user"),
                    "is_active": bool(payload.get("activate", False)),
                    "is_default": False,
                    "created_at": "2026-11-15T00:05:00+00:00",
                    "updated_at": "2026-11-15T00:05:00+00:00",
                }
                profiles_store.append(new_prof)
                route.fulfill(status=201, content_type="application/json", body=json.dumps(new_prof))
                return

            if method == "POST" and "/activate" in url:
                prof_id = url.split("/api/profiles/")[1].split("/activate")[0]
                target = None
                for prof in profiles_store:
                    prof["is_active"] = prof["id"] == prof_id
                    if prof["id"] == prof_id:
                        target = prof
                route.fulfill(status=200, content_type="application/json", body=json.dumps(target))
                return

            if method == "DELETE" and "/api/profiles/" in url:
                prof_id = url.rsplit("/", 1)[-1]
                profiles_store[:] = [prof for prof in profiles_store if prof["id"] != prof_id]
                profiles_store[0]["is_active"] = True
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "deleted": True,
                            "profile_id": prof_id,
                            "active_profile_id": "default",
                        }
                    ),
                )
                return

            route.fallback_()

        page.route("**/api/profiles**", handle_profiles)
        page.route(
            "**/api/personalization**",
            lambda r: r.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"preferences": [], "total": 0, "limit": 100, "offset": 0, "categories": []}),
            ),
        )

        page.goto(ui_static_server)
        switcher = page.get_by_test_id("profile-switcher")
        switcher.wait_for(state="visible", timeout=10000)

        # Navigate to Settings -> ProfileManagementCard
        page.locator("button.nav-item:has-text('Settings')").click()
        card = page.get_by_test_id("profile-management-card")
        card.wait_for(state="visible", timeout=10000)

        # Default profile must NOT have a Delete button
        assert page.get_by_label("Delete profile Default").count() == 0

        # Create Work profile
        page.get_by_test_id("add-profile-btn").click()
        page.get_by_label("Profile Name").fill("Work")
        page.get_by_label("Profile Description").fill("Engineering workspace")
        page.get_by_test_id("save-profile-btn").click()

        page.get_by_test_id("profile-item-prof_work_1").wait_for(state="visible", timeout=5000)

        # Switch back to Default using Header profile switcher
        page.get_by_label("Switch Active Profile").select_option("default")
        page.get_by_label("Activate profile Work").wait_for(state="visible", timeout=5000)

        # Two-step delete Work profile
        page.get_by_label("Delete profile Work").click()
        page.get_by_label("Confirm delete profile Work").click()
        page.get_by_text("Profile and its isolated local data deleted.").wait_for(state="visible", timeout=5000)

        browser.close()
