"""
VoxPilot - Asynchronous Database Connection and Engine
Manages SQLite async engine with aiosqlite and SQLModel metadata initialization.
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path
from typing import Optional, AsyncGenerator, Generator, Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.engine import Engine, Connection
from sqlalchemy import event, text

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Enforce SQLite foreign key constraints and Write-Ahead Logging (WAL) on all connections."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.close()
    except Exception as exc:
        logger.debug(f"SQLite PRAGMA configuration skipped or unsupported: {exc}")


# Default persistent database path anchored to unified path resolution
from backend.paths import get_database_path

DEFAULT_DB_FILE = get_database_path()
DEFAULT_DATA_DIR = DEFAULT_DB_FILE.parent

_ASYNC_ENGINE: Optional[AsyncEngine] = None
_SESSION_MAKER: Optional[async_sessionmaker[AsyncSession]] = None
_SYNC_ENGINE: Optional[Engine] = None


def get_default_db_url(async_driver: bool = True) -> str:
    """Retrieve normalized SQLite database connection URL."""
    custom_url = os.environ.get("VOXPILOT_DB_URL")
    if custom_url:
        if async_driver and "+aiosqlite" not in custom_url and "sqlite" in custom_url:
            return custom_url.replace("sqlite://", "sqlite+aiosqlite://")
        elif not async_driver and "+aiosqlite" in custom_url:
            return custom_url.replace("sqlite+aiosqlite://", "sqlite://")
        return custom_url

    custom_path = os.environ.get("VOXPILOT_DB_PATH")
    if custom_path:
        db_path = Path(custom_path).resolve()
    else:
        db_path = get_database_path()

    # Ensure parent directory exists for file-based SQLite
    if not str(db_path).startswith(":memory:"):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    if async_driver:
        return f"sqlite+aiosqlite:///{db_path}"
    return f"sqlite:///{db_path}"


def get_async_engine(db_url: Optional[str] = None) -> AsyncEngine:
    """Retrieve or initialize shared AsyncEngine singleton."""
    global _ASYNC_ENGINE, _SESSION_MAKER
    if _ASYNC_ENGINE is None or db_url is not None:
        url = db_url or get_default_db_url(async_driver=True)
        _ASYNC_ENGINE = create_async_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
        _SESSION_MAKER = async_sessionmaker(
            _ASYNC_ENGINE,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        logger.info(f"Initialized async database engine: {url}")
    return _ASYNC_ENGINE


def get_sync_engine(db_url: Optional[str] = None) -> Engine:
    """Retrieve or initialize synchronous SQLite engine for background/sync callers."""
    global _SYNC_ENGINE
    if _SYNC_ENGINE is None or db_url is not None:
        url = db_url or get_default_db_url(async_driver=False)
        _SYNC_ENGINE = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
        logger.info(f"Initialized sync database engine: {url}")
    return _SYNC_ENGINE


def get_sessionmaker(engine: Optional[AsyncEngine] = None) -> async_sessionmaker[AsyncSession]:
    """Retrieve or initialize shared AsyncSession factory."""
    global _SESSION_MAKER
    if _SESSION_MAKER is None or engine is not None:
        active_engine = engine or get_async_engine()
        _SESSION_MAKER = async_sessionmaker(
            active_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _SESSION_MAKER


_FTS5_AVAILABLE: Optional[bool] = None


def _get_table_columns(conn: Connection, table_name: str) -> set[str]:
    """Return existing column names for an SQLite table."""
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {str(row[1]) for row in rows if len(row) > 1}


def _table_exists(conn: Connection, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = :name"),
        {"name": table_name},
    ).fetchone()
    return row is not None


def run_migrations_with_connection(conn: Connection) -> Dict[str, Any]:
    """Idempotently create and migrate VoxPilot SQLite tables and FTS5 index.

    Safe to run repeatedly on both fresh databases and existing installations
    without dropping tables or altering existing user rows.
    """
    global _FTS5_AVAILABLE
    # Ensure all SQLModel models are registered on metadata before create_all
    import backend.database.models  # noqa: F401

    SQLModel.metadata.create_all(conn)

    applied_steps: list[str] = []

    # 1. Migrate legacy ConversationRecord columns without data loss
    if _table_exists(conn, "conversationrecord"):
        conv_cols = _get_table_columns(conn, "conversationrecord")
        if "profile_id" not in conv_cols:
            conn.execute(
                text("ALTER TABLE conversationrecord ADD COLUMN profile_id TEXT DEFAULT 'default'")
            )
            conn.execute(
                text("UPDATE conversationrecord SET profile_id = 'default' WHERE profile_id IS NULL")
            )
            applied_steps.append("added_conversationrecord.profile_id")
        if "active_context_json" not in conv_cols:
            conn.execute(
                text("ALTER TABLE conversationrecord ADD COLUMN active_context_json TEXT")
            )
            applied_steps.append("added_conversationrecord.active_context_json")
        conn.execute(
            text("UPDATE conversationrecord SET profile_id = 'default' WHERE profile_id IS NULL OR TRIM(profile_id) = ''")
        )

    # 1b. Migrate legacy ExecutionRecord.profile_id from ConversationRecord without data loss
    if _table_exists(conn, "executionrecord"):
        exec_cols = _get_table_columns(conn, "executionrecord")
        if "profile_id" not in exec_cols:
            conn.execute(
                text("ALTER TABLE executionrecord ADD COLUMN profile_id TEXT DEFAULT 'default'")
            )
            applied_steps.append("added_executionrecord.profile_id")
        if _table_exists(conn, "conversationrecord"):
            conn.execute(
                text(
                    "UPDATE executionrecord "
                    "SET profile_id = COALESCE("
                    "  (SELECT c.profile_id FROM conversationrecord c "
                    "   WHERE c.conversation_id = executionrecord.conversation_id), "
                    "  'default'"
                    ") "
                    "WHERE profile_id IS NULL OR TRIM(profile_id) = '' OR profile_id = 'default'"
                )
            )
        else:
            conn.execute(
                text("UPDATE executionrecord SET profile_id = 'default' WHERE profile_id IS NULL OR TRIM(profile_id) = ''")
            )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_executionrecord_profile_id ON executionrecord (profile_id)")
        )

    # 1c. Ensure ProfileRecord default profile & unique normalized_name index
    if _table_exists(conn, "profilerecord"):
        prof_cols = _get_table_columns(conn, "profilerecord")
        if "theme_mode" not in prof_cols:
            conn.execute(
                text("ALTER TABLE profilerecord ADD COLUMN theme_mode TEXT NOT NULL DEFAULT 'dark'")
            )
            applied_steps.append("added_profilerecord.theme_mode")
        if "accent_color" not in prof_cols:
            conn.execute(
                text("ALTER TABLE profilerecord ADD COLUMN accent_color TEXT NOT NULL DEFAULT 'blue'")
            )
            applied_steps.append("added_profilerecord.accent_color")
        conn.execute(
            text("UPDATE profilerecord SET theme_mode = 'dark' WHERE theme_mode IS NULL OR TRIM(theme_mode) = ''")
        )
        conn.execute(
            text("UPDATE profilerecord SET accent_color = 'blue' WHERE accent_color IS NULL OR TRIM(accent_color) = ''")
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_profilerecord_normalized_name "
                "ON profilerecord (normalized_name)"
            )
        )
        default_exists = conn.execute(
            text("SELECT 1 FROM profilerecord WHERE id = 'default'")
        ).first()
        if not default_exists:
            conn.execute(
                text(
                    "INSERT INTO profilerecord "
                    "(id, name, normalized_name, description, avatar, theme_mode, accent_color, is_active, is_default, created_at, updated_at) "
                    "VALUES ('default', 'Default', 'default', 'Default local VoxPilot profile', 'user', 'dark', 'blue', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            applied_steps.append("created_default_profile")
        else:
            conn.execute(
                text("UPDATE profilerecord SET is_default = 1 WHERE id = 'default'")
            )

        active_row = conn.execute(
            text("SELECT id FROM profilerecord WHERE is_active = 1 LIMIT 1")
        ).first()
        if not active_row:
            conn.execute(
                text("UPDATE profilerecord SET is_active = 1 WHERE id = 'default'")
            )
            applied_steps.append("activated_default_profile")

        if _table_exists(conn, "profile_provider_configs"):
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO profile_provider_configs "
                    "(profile_id, active_provider_id, local_model, config_json, updated_at) "
                    "SELECT id, 'local', 'qwen3:4b', '{}', CURRENT_TIMESTAMP FROM profilerecord"
                )
            )
            applied_steps.append("ensured_profile_provider_configs")

    # 2. Ensure composite index on memoryrecord (profile_id, normalized_content)
    if _table_exists(conn, "memoryrecord"):
        conn.execute(
            text("UPDATE memoryrecord SET profile_id = 'default' WHERE profile_id IS NULL OR TRIM(profile_id) = ''")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memoryrecord_profile_norm "
                "ON memoryrecord (profile_id, normalized_content)"
            )
        )

    # 2b. Ensure preferencerecord columns & composite index (profile_id, category, normalized_key)
    if _table_exists(conn, "preferencerecord"):
        pref_cols = _get_table_columns(conn, "preferencerecord")
        if "enabled" not in pref_cols:
            conn.execute(
                text("ALTER TABLE preferencerecord ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1")
            )
            applied_steps.append("added_preferencerecord.enabled")
        if "priority" not in pref_cols:
            conn.execute(
                text("ALTER TABLE preferencerecord ADD COLUMN priority INTEGER NOT NULL DEFAULT 3")
            )
            applied_steps.append("added_preferencerecord.priority")
        if "description" not in pref_cols:
            conn.execute(
                text("ALTER TABLE preferencerecord ADD COLUMN description TEXT")
            )
            applied_steps.append("added_preferencerecord.description")
        if "metadata_json" not in pref_cols:
            conn.execute(
                text("ALTER TABLE preferencerecord ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            )
            applied_steps.append("added_preferencerecord.metadata_json")

        conn.execute(
            text("UPDATE preferencerecord SET profile_id = 'default' WHERE profile_id IS NULL OR TRIM(profile_id) = ''")
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_preferencerecord_profile_cat_key "
                "ON preferencerecord (profile_id, category, normalized_key)"
            )
        )
        applied_steps.append("ensured_preferencerecord_index")

    # 3. Create FTS5 virtual table for local full-text memory search (graceful fallback if unavailable)
    fts_available = False
    try:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                "memory_id UNINDEXED, "
                "content, "
                "memory_type UNINDEXED, "
                "profile_id UNINDEXED"
                ")"
            )
        )
        fts_available = True
        applied_steps.append("ensured_memories_fts")

        # Idempotent backfill of any MemoryRecord rows missing from memories_fts
        if _table_exists(conn, "memoryrecord"):
            conn.execute(
                text(
                    "INSERT INTO memories_fts (memory_id, content, memory_type, profile_id) "
                    "SELECT m.id, m.content, m.memory_type, m.profile_id "
                    "FROM memoryrecord m "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM memories_fts f WHERE f.memory_id = m.id"
                    ")"
                )
            )
    except Exception as fts_err:
        fts_available = False
        logger.warning(f"SQLite FTS5 unavailable; falling back to LIKE search: {fts_err}")

    # 4. Non-destructively normalize legacy malformed JSON payloads in execution tables
    from backend.database.models import normalize_plan_steps, serialize_sanitized_json

    repaired_plans = 0
    if _table_exists(conn, "executionrecord"):
        exec_rows = conn.execute(
            text(
                "SELECT execution_id, plan_json FROM executionrecord "
                "WHERE plan_json IS NOT NULL AND TRIM(plan_json) != '' AND plan_json != '[]'"
            )
        ).fetchall()
        for exec_id, raw_plan_json in exec_rows:
            needs_repair = False
            try:
                parsed_plan = json.loads(raw_plan_json)
                if not isinstance(parsed_plan, list) or any(
                    not isinstance(item, dict)
                    or ("parameters" in item and not isinstance(item.get("parameters"), dict))
                    for item in parsed_plan
                ):
                    needs_repair = True
            except Exception:
                needs_repair = True

            if needs_repair:
                normalized = normalize_plan_steps(raw_plan_json)
                repaired_json = serialize_sanitized_json(normalized) or "[]"
                conn.execute(
                    text("UPDATE executionrecord SET plan_json = :norm WHERE execution_id = :eid"),
                    {"norm": repaired_json, "eid": exec_id},
                )
                repaired_plans += 1
        if repaired_plans:
            applied_steps.append(f"repaired_legacy_execution_plans:{repaired_plans}")
            logger.info("Non-destructively migrated %d legacy ExecutionRecord.plan_json rows.", repaired_plans)

    repaired_step_params = 0
    if _table_exists(conn, "executionsteprecord"):
        step_rows = conn.execute(
            text(
                "SELECT step_id, parameters_json FROM executionsteprecord "
                "WHERE parameters_json IS NOT NULL AND TRIM(parameters_json) != '' AND parameters_json != '{}'"
            )
        ).fetchall()
        for step_id, raw_params_json in step_rows:
            needs_step_repair = False
            repaired_params: Any = None
            try:
                parsed_params = json.loads(raw_params_json)
                if not isinstance(parsed_params, dict):
                    needs_step_repair = True
                    repaired_params = {"_legacy_value": parsed_params}
            except Exception:
                needs_step_repair = True
                repaired_params = {"_legacy_raw": str(raw_params_json)}

            if needs_step_repair:
                repaired_json = serialize_sanitized_json(repaired_params) or "{}"
                conn.execute(
                    text("UPDATE executionsteprecord SET parameters_json = :norm WHERE step_id = :sid"),
                    {"norm": repaired_json, "sid": step_id},
                )
                repaired_step_params += 1
        if repaired_step_params:
            applied_steps.append(f"repaired_legacy_step_parameters:{repaired_step_params}")

    _FTS5_AVAILABLE = fts_available
    return {
        "fts5_available": fts_available,
        "applied_steps": applied_steps,
    }


def run_migrations_sync(engine: Optional[Engine] = None) -> Dict[str, Any]:
    """Run idempotent schema and FTS5 migrations on a synchronous SQLAlchemy engine."""
    target_engine = engine or get_sync_engine()
    with target_engine.begin() as conn:
        return run_migrations_with_connection(conn)


def is_fts5_available(engine: Optional[Engine] = None) -> bool:
    """Return whether SQLite FTS5 virtual table support is available on this database."""
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is not None and engine is None:
        return _FTS5_AVAILABLE
    target_engine = engine or get_sync_engine()
    with target_engine.begin() as conn:
        if _table_exists(conn, "memories_fts"):
            _FTS5_AVAILABLE = True
            return True
        res = run_migrations_with_connection(conn)
        return bool(res.get("fts5_available", False))


async def init_db(db_url: Optional[str] = None) -> None:
    """Create and migrate all SQLModel tables and FTS5 index safely."""
    engine = get_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(run_migrations_with_connection)
    logger.info("Database schema and migrations initialized successfully.")


def init_db_sync(db_url: Optional[str] = None) -> None:
    """Synchronous database schema creation and idempotent migration."""
    engine = get_sync_engine(db_url)
    run_migrations_sync(engine)
    logger.info("Database schema and migrations initialized synchronously.")


async def close_db() -> None:
    """Dispose active engine connection pools gracefully."""
    global _ASYNC_ENGINE, _SESSION_MAKER, _SYNC_ENGINE, _FTS5_AVAILABLE
    if _ASYNC_ENGINE is not None:
        await _ASYNC_ENGINE.dispose()
        _ASYNC_ENGINE = None
        _SESSION_MAKER = None
    if _SYNC_ENGINE is not None:
        _SYNC_ENGINE.dispose()
        _SYNC_ENGINE = None
    _FTS5_AVAILABLE = None
    logger.info("Database engines disposed.")


def reset_db_connection() -> None:
    """Synchronous reset helper for test fixtures."""
    global _ASYNC_ENGINE, _SESSION_MAKER, _SYNC_ENGINE, _FTS5_AVAILABLE
    if _SYNC_ENGINE is not None:
        try:
            _SYNC_ENGINE.dispose()
        except Exception:
            pass
    _ASYNC_ENGINE = None
    _SESSION_MAKER = None
    _SYNC_ENGINE = None
    _FTS5_AVAILABLE = None


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI async dependency yielding an AsyncSession."""
    maker = get_sessionmaker()
    async with maker() as session:
        yield session
