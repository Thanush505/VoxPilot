"""
VoxPilot - Database Subsystem
Provides SQLModel persistent storage for conversations, executions, and execution steps.
"""

from backend.database.models import (
    ConversationRecord,
    ExecutionRecord,
    ExecutionStepRecord,
    serialize_sanitized_json,
    sanitize_dict_for_storage,
)
from backend.database.connection import (
    get_async_engine,
    get_sessionmaker,
    init_db,
    init_db_sync,
    close_db,
    reset_db_connection,
    get_async_session,
    get_sync_engine,
    get_default_db_url,
)
from backend.database.repository import DatabaseRepository

__all__ = [
    "ConversationRecord",
    "ExecutionRecord",
    "ExecutionStepRecord",
    "serialize_sanitized_json",
    "sanitize_dict_for_storage",
    "get_async_engine",
    "get_sync_engine",
    "get_sessionmaker",
    "init_db",
    "init_db_sync",
    "close_db",
    "reset_db_connection",
    "get_async_session",
    "get_default_db_url",
    "DatabaseRepository",
]
