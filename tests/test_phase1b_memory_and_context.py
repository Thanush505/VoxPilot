"""
Comprehensive Phase 1B Test Suite:
- Idempotent SQLite Schema Migrations & Legacy Data Preservation
- FTS5 Virtual Table Creation & Synchronization (memories_fts)
- LongTermMemoryService (CRUD, Pagination, Deduplication, Search, last_accessed_at)
- Secret Scrubbing & Conservative MemoryExtractor
- ConversationHydrator & Multi-Session Isolation / Restart Recovery
- Real LLM Response Generation + ModelManager.track_inference() Lifecycle Lock
- Memory REST API Endpoints (/api/memory & /agent/memory)
- Frontend Settings Page Local AI Memory Management UI (Playwright)
"""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Browser, sync_playwright
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.agent.graph import VoxPilotAgent
from backend.agent.llm import OllamaLLM
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import ToolRegistry
from backend.ai.model_manager import (
    ActiveInferenceConflictError,
    get_shared_model_manager,
    reset_shared_model_manager,
)
from backend.automation.confirmation_manager import ConfirmationManager
from backend.database.connection import is_fts5_available, run_migrations_sync
from backend.database.repository import DatabaseRepository
from backend.main import app
from backend.memory.context_manager import (
    ConversationContext,
    ConversationContextManager,
    ConversationTurn,
)
from backend.memory.conversation_hydrator import ConversationHydrator
from backend.memory.long_term_memory import (
    InvalidMemoryTypeError,
    LongTermMemoryService,
    MemoryPrivacyError,
    reset_shared_long_term_memory_service,
)
from backend.memory.memory_extractor import MemoryExtractor
from tests.test_frontend_browser import (
    FRONTEND_DIST,
    StaticSpaHandler,
    setup_default_mock_routes,
)


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------

@pytest.fixture
def isolated_db(tmp_path: Path):
    """Create an isolated temporary SQLite database repository + memory service."""
    db_file = tmp_path / "phase1b_test.db"
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"

    sync_engine = create_engine(sync_url, echo=False)
    async_engine = create_async_engine(async_url, echo=False)
    session_maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    run_migrations_sync(sync_engine)
    repo = DatabaseRepository(session_maker=session_maker, sync_engine=sync_engine)
    mem_svc = LongTermMemoryService(sync_engine=sync_engine)

    yield {
        "db_file": db_file,
        "sync_engine": sync_engine,
        "session_maker": session_maker,
        "repository": repo,
        "memory_service": mem_svc,
    }
    sync_engine.dispose()


# ==============================================================================
# 1. Database Migration & Legacy Data Preservation Tests
# ==============================================================================

def test_idempotent_migration_preserves_legacy_data(tmp_path: Path):
    """Verify migration safely alters pre-Phase-1B DBs without losing existing rows."""
    legacy_db = tmp_path / "legacy_voxpilot.db"
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE conversationrecord (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO conversationrecord (conversation_id, title, summary, created_at, updated_at)
        VALUES ('conv_legacy_1', 'Legacy Session', 'Preserved summary', '2026-10-01T10:00:00', '2026-10-01T10:05:00');
        """
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{legacy_db}", echo=False)
    try:
        res1 = run_migrations_sync(engine)
        assert "added_conversationrecord.profile_id" in res1["applied_steps"]
        assert "added_conversationrecord.active_context_json" in res1["applied_steps"]
        assert res1["fts5_available"] is True

        # Second migration run must be completely idempotent with zero data loss
        res2 = run_migrations_sync(engine)
        assert res2["fts5_available"] is True
        assert "added_conversationrecord.profile_id" not in res2["applied_steps"]

        with engine.begin() as c:
            row = c.execute(
                text(
                    "SELECT conversation_id, title, summary, profile_id, active_context_json "
                    "FROM conversationrecord WHERE conversation_id = 'conv_legacy_1'"
                )
            ).fetchone()
            assert row is not None
            assert row[0] == "conv_legacy_1"
            assert row[1] == "Legacy Session"
            assert row[2] == "Preserved summary"
            assert row[3] == "default"
            assert row[4] is None

            fts_exists = c.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'")
            ).fetchone()
            assert fts_exists is not None
    finally:
        engine.dispose()


def test_fts5_synchronization_on_insert_update_delete(isolated_db):
    """Verify memories_fts stays synchronized across insert, update, and delete."""
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]
    engine = isolated_db["sync_engine"]
    assert is_fts5_available(engine) is True

    created = mem_svc.create_memory(
        content="My preferred editor is Visual Studio Code.",
        memory_type="user_preference",
        importance=4,
    )
    mem_id = created["id"]

    with engine.begin() as c:
        row = c.execute(
            text("SELECT memory_id, content FROM memories_fts WHERE memory_id = :mid"),
            {"mid": mem_id},
        ).fetchone()
        assert row is not None
        assert "Visual Studio Code" in row[1]

    # Update memory -> FTS5 must reflect updated content and drop stale tokens
    mem_svc.update_memory(mem_id, content="My preferred editor is Neovim.")
    assert len(mem_svc.search_memories("Neovim")) == 1
    assert len(mem_svc.search_memories("Visual Studio Code")) == 0

    # Delete memory -> FTS5 row must be removed too
    assert mem_svc.delete_memory(mem_id) is True
    with engine.begin() as c:
        row_after = c.execute(
            text("SELECT memory_id FROM memories_fts WHERE memory_id = :mid"),
            {"mid": mem_id},
        ).fetchone()
        assert row_after is None


# ==============================================================================
# 2. LongTermMemoryService CRUD, Deduplication, Pagination & Search Tests
# ==============================================================================

def test_long_term_memory_crud_dedup_and_pagination(isolated_db):
    """Verify create, deduplication, get with last_accessed_at, pagination, and search."""
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]

    m1 = mem_svc.create_memory(
        content="I prefer dark mode.",
        memory_type="user_preference",
        importance=3,
        source_conversation_id="conv_ui_1",
    )
    assert m1["deduplicated"] is False
    assert m1["source_conversation_id"] == "conv_ui_1"

    # Normalized duplicate updates existing item instead of duplicating rows
    m1_dup = mem_svc.create_memory(
        content="  i prefer DARK mode  ",
        memory_type="user_preference",
        importance=5,
        source_conversation_id="conv_ui_2",
    )
    assert m1_dup["deduplicated"] is True
    assert m1_dup["id"] == m1["id"]
    assert m1_dup["importance"] == 5

    mem_svc.create_memory(content="My project is called VoxPilot.", memory_type="project_context", importance=5)
    mem_svc.create_memory(content="Always keep responses concise.", memory_type="instruction", importance=4)

    page1 = mem_svc.list_memories(limit=2, offset=0)
    page2 = mem_svc.list_memories(limit=2, offset=2)
    assert page1["total"] == 3
    assert len(page1["memories"]) == 2
    assert len(page2["memories"]) == 1

    fetched = mem_svc.get_memory(m1["id"], touch=True)
    assert fetched is not None
    assert fetched["last_accessed_at"] is not None

    # Special punctuation and FTS5 keyword syntax in search query must never crash FTS5 MATCH
    special_results = mem_svc.search_memories('VoxPilot?!* :() ""')
    assert any("VoxPilot" in item["content"] for item in special_results)
    assert isinstance(mem_svc.search_memories("NOT OR AND NEAR *"), list)

    with pytest.raises(InvalidMemoryTypeError):
        mem_svc.create_memory(content="Valid text", memory_type="unsupported_category")


# ==============================================================================
# 3. Secret Scrubbing & Conservative MemoryExtractor Tests
# ==============================================================================

@pytest.mark.parametrize(
    "secret_payload",
    [
        "My password is SuperSecretPassword123!",
        "My OpenAI key is sk-proj-abcdef1234567890abcdef",
        "GitHub token: ghp_1234567890abcdefghij1234567890abcd",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjMifQ.signature123",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
        "Connect to postgresql://admin:secretpass@127.0.0.1:5432/voxpilot",
    ],
)
def test_secret_rejection_in_service_and_extractor(isolated_db, secret_payload: str):
    """Secrets, keys, passwords, and tokens must never be stored in persistent memory."""
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]
    extractor = MemoryExtractor(memory_service=mem_svc)

    with pytest.raises(MemoryPrivacyError):
        mem_svc.create_memory(content=secret_payload, memory_type="user_fact")

    extracted = extractor.extract_and_store(secret_payload, conversation_id="conv_sec")
    assert extracted == []
    assert mem_svc.list_memories()["total"] == 0


def test_conservative_memory_extractor_filtering(isolated_db):
    """Extractor saves durable user facts/preferences and rejects noisy or command turns."""
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]
    extractor = MemoryExtractor(memory_service=mem_svc)

    # Should NOT extract questions, commands, greetings, or hypotheticals
    noisy_inputs = [
        "Open Chrome",
        "What is my preferred programming language?",
        "Hello VoxPilot!",
        "What if I preferred Rust instead of Python?",
        "Launch Notepad and type hello",
        "line 1\nline 2\nline 3 pasted log dump",
    ]
    for msg in noisy_inputs:
        assert extractor.extract_and_store(msg, conversation_id="conv_noise") == []

    # SHOULD extract explicit preferences, facts, instructions, and project context
    s1 = extractor.extract_and_store("My preferred programming language is Python.", conversation_id="conv_pref")
    s2 = extractor.extract_and_store("I prefer dark mode.", conversation_id="conv_pref")
    s3 = extractor.extract_and_store("My project is called VoxPilot.", conversation_id="conv_pref")
    s4 = extractor.extract_and_store("Always respond concisely.", conversation_id="conv_pref")

    assert len(s1) == 1 and s1[0]["memory_type"] == "user_preference"
    assert len(s2) == 1 and s2[0]["memory_type"] == "user_preference"
    assert len(s3) == 1 and s3[0]["memory_type"] == "project_context"
    assert len(s4) == 1 and s4[0]["memory_type"] == "instruction"
    assert mem_svc.list_memories()["total"] == 4


# ==============================================================================
# 4. Conversation Context Persistence, Isolation & Corruption Recovery
# ==============================================================================

def test_conversation_hydrator_save_restore_isolation_and_corrupt_json(isolated_db, tmp_path: Path):
    """Hydrator persists context across restarts, enforces isolation, and tolerates bad JSON."""
    repo: DatabaseRepository = isolated_db["repository"]
    hydrator = ConversationHydrator(repository=repo)

    real_file = tmp_path / "notes.txt"
    real_file.write_text("hello", encoding="utf-8")

    mgr1 = ConversationContextManager(window_validator=lambda h: h == 4242)
    ctx_a = mgr1.get_context("conv_alpha")
    ctx_a.active_window = {"hwnd": 4242, "app_name": "notepad", "title": "notes.txt - Notepad"}
    ctx_a.recent_application = "notepad"
    ctx_a.recent_files = [str(real_file), str(tmp_path / "deleted.txt")]
    ctx_a.recent_urls = ["https://docs.python.org"]
    ctx_a.add_turn(
        ConversationTurn(
            turn_id="turn_1",
            user_input="Open notes.txt",
            intent="file_management",
            plan_summary=["launch_application: notepad"],
            completed_actions=["launch_application"],
            response="Opened Notepad with notes.txt.",
            status="completed",
        )
    )
    assert hydrator.save_context("conv_alpha", ctx_a) is True

    # Separate session conv_beta has completely distinct context
    ctx_b = mgr1.get_context("conv_beta")
    ctx_b.recent_application = "calculator"
    ctx_b.add_turn(
        ConversationTurn(
            turn_id="turn_1",
            user_input="Open calculator",
            intent="open_application",
            plan_summary=["launch_application: calculator"],
            completed_actions=["launch_application"],
            response="Opened Calculator.",
            status="completed",
        )
    )
    assert hydrator.save_context("conv_beta", ctx_b) is True

    # Simulate fresh process restart with a brand new ConversationContextManager
    mgr2 = ConversationContextManager(window_validator=lambda h: h == 4242)
    restored_a = hydrator.hydrate_context("conv_alpha", mgr2, revalidate_os_handles=True)
    restored_b = hydrator.hydrate_context("conv_beta", mgr2, revalidate_os_handles=True)

    assert restored_a.recent_application == "notepad"
    assert restored_a.active_window_hwnd == 4242
    # Missing file deleted.txt evicted; existing real_file preserved
    assert restored_a.recent_files == [str(real_file)]
    assert len(restored_a.turns) == 1
    assert restored_a.turns[0].user_input == "Open notes.txt"

    assert restored_b.recent_application == "calculator"
    assert restored_b.active_window is None
    assert len(restored_b.turns) == 1
    assert restored_b.turns[0].user_input == "Open calculator"

    # Simulate stale closed HWND -> evicted safely
    mgr_stale = ConversationContextManager(window_validator=lambda h: False)
    restored_stale = hydrator.hydrate_context("conv_alpha", mgr_stale, revalidate_os_handles=True)
    assert restored_stale.active_window is None

    # Corrupt JSON in DB -> does not crash and safely reconstructs from ExecutionRecord
    repo.save_execution_sync({
        "execution_id": "exec_recover_1",
        "conversation_id": "conv_corrupt",
        "user_input": "Check system info",
        "intent": "system_information",
        "execution_status": "completed",
        "plan": [{"action": "get_system_info", "description": "Check", "parameters": {}}],
        "response": "CPU 10%",
    })
    with isolated_db["sync_engine"].begin() as c:
        c.execute(
            text("UPDATE conversationrecord SET active_context_json = '{bad json' WHERE conversation_id = 'conv_corrupt'")
        )

    mgr3 = ConversationContextManager()
    recovered = hydrator.hydrate_context("conv_corrupt", mgr3, revalidate_os_handles=False)
    assert len(recovered.turns) == 1
    assert recovered.turns[0].user_input == "Check system info"


# ==============================================================================
# 5. LLM Response Generation & ModelManager.track_inference Integration
# ==============================================================================

def test_agent_llm_response_generation_and_model_manager_inference_lock(isolated_db):
    """Verify VoxPilotAgent uses OllamaLLM, injects memories, and locks ModelManager during inference."""
    reset_shared_model_manager()
    mm = get_shared_model_manager()
    observed_prompts = []
    lock_verified = {"active_during_chat": False, "unload_blocked": False}

    def fake_ollama_chat(model, messages, **kwargs):
        observed_prompts.append({"model": model, "messages": messages})
        active, _ = mm.is_inference_active("qwen3:4b")
        lock_verified["active_during_chat"] = active and mm._active_inference_counts.get("qwen3:4b", 0) == 1
        try:
            mm.unload_model("qwen3:4b")
        except ActiveInferenceConflictError:
            lock_verified["unload_blocked"] = True
        return types.SimpleNamespace(
            message=types.SimpleNamespace(
                content="<think>internal reasoning</think>Your preferred programming language is **Python**, and you prefer dark mode."
            )
        )

    repo: DatabaseRepository = isolated_db["repository"]
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)
    ctx_mgr = ConversationContextManager()

    with patch("backend.agent.llm.ollama.chat", side_effect=fake_ollama_chat):
        agent = VoxPilotAgent(
            model="qwen3:4b",
            tool_registry=reg,
            tool_executor=executor,
            confirmation_manager=cm,
            context_manager=ctx_mgr,
            repository=repo,
            llm=OllamaLLM(model="qwen3:4b"),
            memory_service=mem_svc,
        )

        # Turn 1: User states preference -> extracted locally & stored
        res1 = agent.process_request("My preferred programming language is Python.", conversation_id="conv_llm_1")
        assert res1["execution_status"] == "completed"
        assert lock_verified["active_during_chat"] is True
        assert lock_verified["unload_blocked"] is True
        assert mm._active_inference_counts.get("qwen3:4b", 0) == 0
        assert mm.is_inference_active("qwen3:4b")[0] is False

        # Turn 2: Simulated restart (fresh agent + fresh context_manager sharing SQLite repo)
        agent2 = VoxPilotAgent(
            model="qwen3:4b",
            tool_registry=reg,
            tool_executor=executor,
            confirmation_manager=cm,
            context_manager=ConversationContextManager(),
            repository=repo,
            llm=OllamaLLM(model="qwen3:4b"),
            memory_service=mem_svc,
        )
        res2 = agent2.process_request("What is my preferred programming language?", conversation_id="conv_llm_1")
        assert res2["execution_status"] == "completed"
        # Clean response stripped <think> and Markdown **bold**
        assert "<think>" not in res2["response"]
        assert "**" not in res2["response"]
        assert "Python" in res2["response"]

        # Verify persisted long-term memory AND prior turn were injected into system prompt
        second_sys_msg = observed_prompts[-1]["messages"][0]["content"]
        assert "My preferred programming language is Python" in second_sys_msg
        assert "Recent turns in this conversation" in second_sys_msg

    # Verify inference lock is ALSO released when ollama.chat raises an error
    with patch("backend.agent.llm.ollama.chat", side_effect=RuntimeError("Ollama connection refused")):
        fallback_agent = VoxPilotAgent(
            model="qwen3:4b",
            tool_registry=reg,
            tool_executor=executor,
            confirmation_manager=cm,
            context_manager=ConversationContextManager(),
            repository=repo,
            llm=OllamaLLM(model="qwen3:4b"),
            memory_service=mem_svc,
        )
        fallback_res = fallback_agent.process_request(
            "What is my preferred programming language?",
            conversation_id="conv_llm_1",
        )
        assert fallback_res["execution_status"] == "completed"
        assert "Python" in fallback_res["response"]
        assert mm._active_inference_counts.get("qwen3:4b", 0) == 0
        assert mm.is_inference_active("qwen3:4b")[0] is False

    reset_shared_model_manager()


# ==============================================================================
# 6. Memory REST API Endpoints (/api/memory & /agent/memory)
# ==============================================================================

def test_memory_rest_api_endpoints(isolated_db):
    """Test GET/POST/PATCH/DELETE and FTS5 /search on /api/memory and /agent/memory."""
    mem_svc: LongTermMemoryService = isolated_db["memory_service"]
    reset_shared_long_term_memory_service()

    with patch("backend.api.memory.get_shared_long_term_memory_service", return_value=mem_svc):
        client = TestClient(app)

        # Reject secrets
        bad_secret = client.post(
            "/api/memory",
            json={"content": "My password is Password1234!", "memory_type": "user_fact"},
        )
        assert bad_secret.status_code == 400

        # Reject invalid type
        bad_type = client.post(
            "/api/memory",
            json={"content": "I like tea.", "memory_type": "invalid_type"},
        )
        assert bad_type.status_code == 400

        # Create valid memory
        c1 = client.post(
            "/api/memory",
            json={
                "content": "I prefer FastAPI over Flask for local services.",
                "memory_type": "user_preference",
                "importance": 4,
            },
        )
        assert c1.status_code == 201
        created = c1.json()
        mem_id = created["id"]
        assert created["memory_type"] == "user_preference"

        # List memories
        listed = client.get("/api/memory?limit=10&offset=0")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["local_only"] is True

        # Search endpoint (including /agent/memory/search alias)
        searched = client.get("/api/memory/search?q=FastAPI")
        assert searched.status_code == 200
        assert searched.json()["count"] == 1

        alias_searched = client.get("/agent/memory/search?q=FastAPI")
        assert alias_searched.status_code == 200
        assert alias_searched.json()["count"] == 1

        # Get single item
        fetched = client.get(f"/api/memory/{mem_id}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == mem_id

        # Patch item
        patched = client.patch(
            f"/api/memory/{mem_id}",
            json={"content": "I strongly prefer FastAPI and SQLModel.", "importance": 5},
        )
        assert patched.status_code == 200
        assert patched.json()["importance"] == 5

        # Delete item
        deleted = client.delete(f"/api/memory/{mem_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        # Confirm 404 after delete
        missing = client.get(f"/api/memory/{mem_id}")
        assert missing.status_code == 404


# ==============================================================================
# 7. Frontend Settings -> Memory Management UI Tests (Playwright)
# ==============================================================================

@pytest.fixture(scope="module")
def memory_ui_static_server():
    import http.server
    import socket
    import threading

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


@pytest.fixture(scope="module")
def memory_headless_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


class TestMemoryManagementUI:
    def test_settings_renders_search_and_deletes_local_memory(
        self, memory_ui_static_server: str, memory_headless_browser: Browser
    ):
        page = memory_headless_browser.new_page()
        setup_default_mock_routes(page, is_connected=True)

        memories_store = [
            {
                "id": "mem_ui_1",
                "memory_id": "mem_ui_1",
                "profile_id": "default",
                "memory_type": "user_preference",
                "content": "My preferred programming language is Python.",
                "normalized_content": "my preferred programming language is python",
                "source_conversation_id": "conv_1",
                "importance": 4,
                "created_at": "2026-11-14T18:00:00Z",
                "updated_at": "2026-11-14T18:00:00Z",
                "last_accessed_at": "2026-11-14T18:00:00Z",
                "metadata": {},
            },
            {
                "id": "mem_ui_2",
                "memory_id": "mem_ui_2",
                "profile_id": "default",
                "memory_type": "project_context",
                "content": "My project is called VoxPilot.",
                "normalized_content": "my project is called voxpilot",
                "source_conversation_id": "conv_1",
                "importance": 5,
                "created_at": "2026-11-14T18:01:00Z",
                "updated_at": "2026-11-14T18:01:00Z",
                "last_accessed_at": None,
                "metadata": {},
            },
        ]

        def handle_memory_routes(route):
            url = route.request.url
            method = route.request.method.upper()
            if "/api/memory/search" in url and method == "GET":
                from urllib.parse import parse_qs, urlparse
                q = (parse_qs(urlparse(url).query).get("q", [""])[0]).lower()
                filtered = [m for m in memories_store if q in m["content"].lower()]
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "query": q,
                        "memories": filtered,
                        "count": len(filtered),
                        "local_only": True,
                    }),
                )
            elif method == "DELETE":
                mem_id = url.rstrip("/").split("/")[-1]
                memories_store[:] = [m for m in memories_store if m["id"] != mem_id]
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": "deleted", "deleted": True, "memory_id": mem_id}),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "memories": memories_store,
                        "total": len(memories_store),
                        "limit": 50,
                        "offset": 0,
                        "local_only": True,
                    }),
                )

        page.route("**/api/memory**", handle_memory_routes)

        try:
            page.goto(memory_ui_static_server)
            page.wait_for_selector("button.nav-item:has-text('Settings')")
            page.locator("button.nav-item:has-text('Settings')").click()

            card = page.locator('[data-testid="memory-management-card"]')
            card.wait_for(state="visible")
            assert "Local AI Memory Management" in card.inner_text()
            assert "Stored Locally Only" in card.inner_text()

            page.wait_for_selector('[data-testid="memory-item-row"]')
            assert card.locator('[data-testid="memory-item-row"]').count() == 2

            # Search filters to VoxPilot
            page.locator('input[aria-label="Search local memories"]').fill("VoxPilot")
            page.wait_for_timeout(400)
            assert "My project is called VoxPilot." in card.inner_text()
            assert "Python" not in card.inner_text()

            # Clear search, then test explicit two-step delete confirmation
            page.locator('button[aria-label="Clear memory search"]').click()
            page.wait_for_timeout(300)
            page.locator('button[aria-label="Delete memory mem_ui_1"]').click()
            confirm_btn = page.locator('button[aria-label="Confirm delete memory mem_ui_1"]')
            confirm_btn.wait_for(state="visible")
            confirm_btn.click()

            page.wait_for_timeout(300)
            assert "My preferred programming language is Python." not in card.inner_text()
        finally:
            page.close()
