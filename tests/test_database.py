"""
Unit and integration tests for VoxPilot SQLite persistence subsystem.
Tests SQLModel entities, DatabaseRepository (async and sync), relationships,
sanitization of tokens/secrets, and isolated multi-session persistence.
"""

import os
import pytest
from pathlib import Path
from datetime import datetime, timezone
from sqlmodel import SQLModel, create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.database.models import (
    ConversationRecord,
    ExecutionRecord,
    ExecutionStepRecord,
    sanitize_dict_for_storage,
    serialize_sanitized_json,
)
from backend.database.connection import (
    init_db,
    init_db_sync,
    close_db,
    get_sync_engine,
    get_async_engine,
)
from backend.database.repository import DatabaseRepository


@pytest.fixture
def temp_db_path(tmp_path: Path):
    """Provide a path to a temporary isolated SQLite database file."""
    db_file = tmp_path / "test_voxpilot.db"
    return db_file


@pytest.fixture
def repo(temp_db_path: Path):
    """Provide a DatabaseRepository instance configured to use a temporary SQLite file."""
    url = f"sqlite:///{temp_db_path}"
    async_url = f"sqlite+aiosqlite:///{temp_db_path}"

    sync_engine = create_engine(url, echo=False)
    async_engine = create_async_engine(async_url, echo=False)
    session_maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    SQLModel.metadata.create_all(sync_engine)

    repository = DatabaseRepository(
        session_maker=session_maker,
        sync_engine=sync_engine,
    )
    yield repository
    sync_engine.dispose()


# ==============================================================================
# Sanitization Utility Tests
# ==============================================================================

def test_sanitize_dict_strips_sensitive_keys():
    """Verify passwords, tokens, secrets, and auth headers are completely stripped."""
    raw = {
        "user_input": "hello",
        "token": "secret_token_12345",
        "confirmation_token": "token_abcde",
        "password": "my_secret_password",
        "api_key": "sk-123456789",
        "safe_param": 42,
        "nested": {
            "auth_token": "sub_token_999",
            "secret_key": "private_key_data",
            "nested_safe": "visible",
        },
    }
    sanitized = sanitize_dict_for_storage(raw)
    assert sanitized["user_input"] == "hello"
    assert "token" not in sanitized
    assert "confirmation_token" not in sanitized
    assert "password" not in sanitized
    assert "api_key" not in sanitized
    assert sanitized["safe_param"] == 42
    assert "auth_token" not in sanitized["nested"]
    assert "secret_key" not in sanitized["nested"]
    assert sanitized["nested"]["nested_safe"] == "visible"


def test_serialize_sanitized_json_encodes_cleanly():
    """Verify serialized JSON contains no raw secrets or tokens."""
    raw = {"token": "secret_abc", "action": "open_app"}
    serialized = serialize_sanitized_json(raw)
    assert "secret_abc" not in serialized
    assert "token" not in serialized
    assert "open_app" in serialized


# ==============================================================================
# Database Initialization Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_init_db_async_and_sync(temp_db_path: Path):
    """Verify schema initialization succeeds via both sync and async methods."""
    sync_url = f"sqlite:///{temp_db_path}"
    async_url = f"sqlite+aiosqlite:///{temp_db_path}"

    init_db_sync(sync_url)
    assert temp_db_path.exists()

    await init_db(async_url)
    assert temp_db_path.exists()


# ==============================================================================
# Conversation Persistence Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_create_or_get_conversation(repo: DatabaseRepository):
    """Creating or retrieving conversations ensures idempotency and correct metadata."""
    conv = await repo.create_or_get_conversation(
        conversation_id="conv_test_1",
        title="Test Conversation",
        summary="A test conversation summary.",
    )
    assert conv.conversation_id == "conv_test_1"
    assert conv.title == "Test Conversation"
    assert conv.summary == "A test conversation summary."

    # Second retrieval returns same record
    conv2 = await repo.create_or_get_conversation(
        conversation_id="conv_test_1",
    )
    assert conv2.conversation_id == "conv_test_1"
    assert conv2.title == "Test Conversation"


@pytest.mark.asyncio
async def test_list_conversations(repo: DatabaseRepository):
    """Listing conversations returns all stored conversations ordered by updated_at desc."""
    await repo.create_or_get_conversation("conv_a", title="Conv A")
    await repo.create_or_get_conversation("conv_b", title="Conv B")

    conversations = await repo.list_conversations(limit=10)
    assert len(conversations) == 2
    ids = {c.conversation_id for c in conversations}
    assert "conv_a" in ids
    assert "conv_b" in ids


# ==============================================================================
# Execution & Step Persistence Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_save_execution_and_retrieval_async(repo: DatabaseRepository):
    """Verify async execution persistence and retrieval with sanitized state."""
    state = {
        "execution_id": "exec_100",
        "conversation_id": "conv_100",
        "user_input": "open notepad",
        "intent": "application_control",
        "confidence": 0.99,
        "execution_status": "completed",
        "response": "Opened notepad.",
        "plan": [{"action": "launch_application", "description": "Launch", "parameters": {"app_name": "notepad"}}],
        "completed_steps": [{"action": "launch_application"}],
        "failed_steps": [],
        "replan_count": 0,
        "replan_history": [],
    }

    record = await repo.save_execution(state)
    assert record.execution_id == "exec_100"
    assert record.conversation_id == "conv_100"
    assert record.execution_status == "completed"

    # Retrieve from repository
    fetched = await repo.get_execution("exec_100")
    assert fetched is not None
    assert fetched.execution_id == "exec_100"
    assert fetched.user_input == "open notepad"
    assert fetched.intent == "application_control"
    assert fetched.execution_status == "completed"


def test_save_execution_sync(repo: DatabaseRepository):
    """Verify synchronous execution persistence for background/sync callers."""
    state = {
        "execution_id": "exec_200",
        "conversation_id": "conv_200",
        "user_input": "show system info",
        "intent": "system_information",
        "confidence": 0.95,
        "execution_status": "completed",
        "response": "System is healthy.",
        "plan": [{"action": "get_system_info", "description": "Info", "parameters": {}}],
        "completed_steps": [{"action": "get_system_info"}],
        "failed_steps": [],
        "replan_count": 0,
        "replan_history": [],
    }

    record = repo.save_execution_sync(state)
    assert record.execution_id == "exec_200"

    fetched = repo.get_execution_sync("exec_200")
    assert fetched is not None
    assert fetched.execution_id == "exec_200"
    assert fetched.execution_status == "completed"


@pytest.mark.asyncio
async def test_record_and_get_execution_steps(repo: DatabaseRepository):
    """Record structured execution steps and verify chronological retrieval."""
    await repo.create_or_get_conversation("conv_300")
    await repo.save_execution({
        "execution_id": "exec_300",
        "conversation_id": "conv_300",
        "user_input": "step test",
        "execution_status": "running",
    })

    step1 = await repo.record_execution_step(
        execution_id="exec_300",
        step_index=0,
        action="find_windows",
        parameters={"title": "Notepad"},
        status="completed",
        result_summary="Found 1 window",
    )
    assert step1.step_id is not None
    assert step1.step_index == 0

    step2 = await repo.record_execution_step(
        execution_id="exec_300",
        step_index=1,
        action="type_text",
        parameters={"text": "hello", "token": "secret_bypass_token"},
        status="completed",
        result_summary="Typed text",
    )
    assert step2.step_index == 1
    # Verify token was stripped from stored step
    assert "secret_bypass_token" not in (step2.parameters_json or "")
    assert "token" not in (step2.parameters_json or "")

    steps = await repo.get_execution_steps("exec_300")
    assert len(steps) == 2
    assert steps[0].action == "find_windows"
    assert steps[1].action == "type_text"


# ==============================================================================
# Non-Existent ID Handling Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_nonexistent_ids_return_none_safely(repo: DatabaseRepository):
    """Non-existent execution IDs return None without raising exceptions."""
    assert await repo.get_execution("nonexistent_exec_xyz") is None
    assert await repo.get_conversation("nonexistent_conv_xyz") is None
    assert await repo.get_execution_steps("nonexistent_exec_xyz") == []
    assert await repo.list_executions_for_conversation("nonexistent_conv_xyz") == []


# ==============================================================================
# Cross-Session Persistence Test
# ==============================================================================

def test_persistence_across_separate_sessions(temp_db_path: Path):
    """Verify that records persist to disk when engine is re-opened."""
    url = f"sqlite:///{temp_db_path}"

    # Session 1: Create engine, insert record, close engine
    engine1 = create_engine(url)
    SQLModel.metadata.create_all(engine1)
    repo1 = DatabaseRepository(sync_engine=engine1)
    repo1.save_execution_sync({
        "execution_id": "exec_session_1",
        "conversation_id": "conv_session_1",
        "user_input": "first session command",
        "execution_status": "completed",
    })
    engine1.dispose()

    # Session 2: Connect with brand new engine instance to same SQLite file
    engine2 = create_engine(url)
    repo2 = DatabaseRepository(sync_engine=engine2)
    record = repo2.get_execution_sync("exec_session_1")
    assert record is not None
    assert record.execution_id == "exec_session_1"
    assert record.user_input == "first session command"
    engine2.dispose()
