"""
VoxPilot - Database Consistency and Concurrency Test Suite
Verifies:
1. SQLite PRAGMA foreign_keys = ON and journal_mode = WAL across all connections.
2. Foreign key constraint enforcement: inserting child entities without valid parent raises IntegrityError.
3. Concurrent asynchronous read/write operations without database locking or corruption.
4. Transaction rollback integrity on mid-operation exceptions.
5. Dual sync/async engine parity: writes from one engine are immediately readable by the other.
6. Persistence across engine connection resets and service restarts.
"""

import asyncio
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.database.connection import (
    get_async_engine,
    get_sync_engine,
    init_db_sync,
    close_db,
    reset_db_connection,
)
from backend.database.models import (
    ConversationRecord,
    ExecutionRecord,
    ExecutionStepRecord,
)
from backend.database.repository import DatabaseRepository


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    """Create an isolated temporary file-based SQLite database for testing."""
    db_file = tmp_path / "test_concurrency.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    sync_url = f"sqlite:///{db_file}"

    monkeypatch.setenv("VOXPILOT_DB_URL", db_url)
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(db_file))

    reset_db_connection()
    init_db_sync(sync_url)

    yield str(db_file), db_url, sync_url

    reset_db_connection()


# ==============================================================================
# 1. PRAGMA Foreign Keys & WAL Journal Mode Enforcement
# ==============================================================================

@pytest.mark.asyncio
async def test_sqlite_pragmas_enabled_on_async_engine(temp_db):
    """Async engine connections must enforce foreign_keys=1 and journal_mode=WAL."""
    _, db_url, _ = temp_db
    engine = get_async_engine(db_url)

    async with engine.connect() as conn:
        fk_result = await conn.execute(text("PRAGMA foreign_keys;"))
        assert fk_result.scalar() == 1, "Foreign keys must be ON"

        jm_result = await conn.execute(text("PRAGMA journal_mode;"))
        assert str(jm_result.scalar()).lower() == "wal", "Journal mode must be WAL"

    await engine.dispose()


def test_sqlite_pragmas_enabled_on_sync_engine(temp_db):
    """Sync engine connections must enforce foreign_keys=1 and journal_mode=WAL."""
    _, _, sync_url = temp_db
    engine = get_sync_engine(sync_url)

    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert fk == 1, "Foreign keys must be ON on sync engine"

        jm = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(jm).lower() == "wal", "Journal mode must be WAL on sync engine"

    engine.dispose()


# ==============================================================================
# 2. Foreign Key Constraint Enforcement
# ==============================================================================

@pytest.mark.asyncio
async def test_foreign_key_violation_raises_integrity_error_async(temp_db):
    """Inserting an ExecutionRecord with a nonexistent conversation_id must fail foreign key check."""
    _, db_url, _ = temp_db
    engine = get_async_engine(db_url)

    async with AsyncSession(engine) as session:
        orphan_execution = ExecutionRecord(
            execution_id="orphan_exec_1",
            conversation_id="nonexistent_conv_xyz",
            user_input="hello",
            execution_status="completed",
        )
        session.add(orphan_execution)
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


def test_foreign_key_violation_raises_integrity_error_sync(temp_db):
    """Inserting an ExecutionStepRecord with a nonexistent execution_id must fail foreign key check."""
    _, _, sync_url = temp_db
    engine = get_sync_engine(sync_url)

    with Session(engine) as session:
        orphan_step = ExecutionStepRecord(
            step_id="orphan_step_1",
            execution_id="nonexistent_execution_abc",
            step_index=0,
            action="launch_application",
            execution_status="completed",
        )
        session.add(orphan_step)
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()


# ==============================================================================
# 3. Concurrent Read/Write Stability
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_async_read_write_operations(temp_db):
    """Multiple concurrent async tasks can read and write without locking or corruption."""
    _, db_url, _ = temp_db
    repo = DatabaseRepository()

    # Pre-create parent conversation
    conv_id = "concurrent_test_conv"
    await repo.create_or_get_conversation(conv_id, title="Concurrency Test")

    async def worker_write(index: int):
        exec_id = f"concurrent_exec_{index}"
        state = {
            "execution_id": exec_id,
            "conversation_id": conv_id,
            "user_input": f"Concurrent task {index}",
            "intent": "system_information",
            "execution_status": "completed",
            "plan": [{"action": "get_system_info", "description": "Check info"}],
            "response": f"Done {index}",
        }
        await repo.save_execution(state)
        # Verify immediately readable
        record = await repo.get_execution(exec_id)
        assert record is not None
        assert record.execution_id == exec_id
        return exec_id

    # Launch 15 concurrent write/read operations
    tasks = [worker_write(i) for i in range(15)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 15
    # Verify all 15 executions are listed under the conversation
    all_execs = await repo.list_executions_for_conversation(conv_id)
    assert len(all_execs) == 15

    await close_db()


# ==============================================================================
# 4. Transaction Rollback Integrity
# ==============================================================================

@pytest.mark.asyncio
async def test_transaction_rollback_on_failure(temp_db):
    """Mid-transaction failure rolls back all partial modifications cleanly."""
    _, db_url, _ = temp_db
    engine = get_async_engine(db_url)

    async with AsyncSession(engine) as session:
        # Create valid parent
        conv = ConversationRecord(conversation_id="rollback_conv", title="Rollback Test")
        session.add(conv)
        await session.commit()

    async with AsyncSession(engine) as session:
        # Valid execution
        valid_exec = ExecutionRecord(
            execution_id="valid_exec",
            conversation_id="rollback_conv",
            user_input="test rollback",
            execution_status="completed",
        )
        session.add(valid_exec)

        # Invalid orphan execution that triggers IntegrityError
        invalid_orphan = ExecutionRecord(
            execution_id="invalid_exec",
            conversation_id="does_not_exist",
            user_input="invalid",
            execution_status="completed",
        )
        session.add(invalid_orphan)

        with pytest.raises(IntegrityError):
            await session.commit()

    # Verify that valid_exec was rolled back and never committed
    async with AsyncSession(engine) as session:
        statement = select(ExecutionRecord).where(ExecutionRecord.execution_id == "valid_exec")
        result = await session.exec(statement)
        assert result.first() is None, "Partially added entity must be rolled back on error"

    await engine.dispose()


# ==============================================================================
# 5. Dual Sync/Async Engine Parity and Data Persistence
# ==============================================================================

@pytest.mark.asyncio
async def test_dual_engine_sync_async_parity(temp_db):
    """A record written by the sync engine is immediately queryable by the async engine."""
    _, db_url, sync_url = temp_db

    # 1. Write conversation and execution via sync repository
    sync_repo = DatabaseRepository()
    sync_repo.create_or_get_conversation_sync("parity_conv", title="Parity Test")
    sync_state = {
        "execution_id": "parity_exec_1",
        "conversation_id": "parity_conv",
        "user_input": "Sync written query",
        "intent": "application_control",
        "execution_status": "completed",
        "plan": [{"action": "launch_application", "description": "Launch app"}],
        "response": "App launched via sync.",
    }
    sync_repo.save_execution_sync(sync_state)

    # 2. Read via async repository on the async engine
    async_repo = DatabaseRepository()
    conv = await async_repo.get_conversation("parity_conv")
    assert conv is not None
    assert conv.title == "Parity Test"

    exec_record = await async_repo.get_execution("parity_exec_1")
    assert exec_record is not None
    assert exec_record.user_input == "Sync written query"
    assert exec_record.response == "App launched via sync."

    await close_db()


@pytest.mark.asyncio
async def test_persistence_across_connection_reopening(temp_db):
    """Closing database engines and re-initializing retains all committed data."""
    _, db_url, _ = temp_db

    # Step 1: Save data
    repo = DatabaseRepository()
    await repo.create_or_get_conversation("persistent_conv_1", title="Persistent Session")
    await repo.save_execution({
        "execution_id": "persist_exec_1",
        "conversation_id": "persistent_conv_1",
        "user_input": "Remember this",
        "intent": "system_information",
        "execution_status": "completed",
        "plan": [],
        "response": "Remembered.",
    })

    # Step 2: Fully close engines
    await close_db()
    reset_db_connection()

    # Step 3: Re-open with new engine connection
    reopened_repo = DatabaseRepository()
    conv = await reopened_repo.get_conversation("persistent_conv_1")
    assert conv is not None
    assert conv.title == "Persistent Session"

    exec_record = await reopened_repo.get_execution("persist_exec_1")
    assert exec_record is not None
    assert exec_record.user_input == "Remember this"

    await close_db()
