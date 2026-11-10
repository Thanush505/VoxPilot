"""
VoxPilot - Asynchronous Database Connection and Engine
Manages SQLite async engine with aiosqlite and SQLModel metadata initialization.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional, AsyncGenerator, Generator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.engine import Engine
from sqlalchemy import event

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Enforce SQLite foreign key constraints and Write-Ahead Logging (WAL) on all connections."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.close()
    except Exception as exc:
        logger.debug(f"SQLite PRAGMA configuration skipped or unsupported: {exc}")


# Default persistent database path anchored to project data directory (CWD independent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_FILE = DEFAULT_DATA_DIR / "voxpilot.db"

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
        db_path = DEFAULT_DB_FILE

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


async def init_db(db_url: Optional[str] = None) -> None:
    """Create all SQLModel tables safely if they do not already exist."""
    engine = get_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Database schema initialized successfully.")


def init_db_sync(db_url: Optional[str] = None) -> None:
    """Synchronous database schema creation."""
    engine = get_sync_engine(db_url)
    SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized synchronously.")


async def close_db() -> None:
    """Dispose active engine connection pools gracefully."""
    global _ASYNC_ENGINE, _SESSION_MAKER, _SYNC_ENGINE
    if _ASYNC_ENGINE is not None:
        await _ASYNC_ENGINE.dispose()
        _ASYNC_ENGINE = None
        _SESSION_MAKER = None
    if _SYNC_ENGINE is not None:
        _SYNC_ENGINE.dispose()
        _SYNC_ENGINE = None
    logger.info("Database engines disposed.")


def reset_db_connection() -> None:
    """Synchronous reset helper for test fixtures."""
    global _ASYNC_ENGINE, _SESSION_MAKER, _SYNC_ENGINE
    if _SYNC_ENGINE is not None:
        try:
            _SYNC_ENGINE.dispose()
        except Exception:
            pass
    _ASYNC_ENGINE = None
    _SESSION_MAKER = None
    _SYNC_ENGINE = None


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI async dependency yielding an AsyncSession."""
    maker = get_sessionmaker()
    async with maker() as session:
        yield session
