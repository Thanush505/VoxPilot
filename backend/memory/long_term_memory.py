"""
VoxPilot - Long-Term Persistent Local Memory Service
Manages local SQLite + FTS5 storage, deduplication, secret validation,
paginated listing, full-text search, and lifecycle updates for persistent AI memories.
All memories remain strictly local in %LOCALAPPDATA%\\VoxPilot\\voxpilot.db.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Sequence
from sqlalchemy import text, func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.database.connection import (
    get_sync_engine,
    run_migrations_sync,
    is_fts5_available,
)
from backend.database.models import (
    MemoryRecord,
    VALID_MEMORY_TYPES,
    contains_obvious_secret,
    sanitize_dict_for_storage,
)

logger = logging.getLogger(__name__)


class MemoryPrivacyError(ValueError):
    """Raised when memory content violates local secret/credential storage rules."""


class InvalidMemoryTypeError(ValueError):
    """Raised when an unsupported memory_type is supplied."""


_SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could",
    "did", "do", "does", "for", "from", "have", "how", "i", "in", "is",
    "it", "me", "my", "of", "on", "or", "please", "tell", "that", "the",
    "this", "to", "what", "when", "where", "which", "who", "will", "with",
    "would", "you", "your",
}


def normalize_memory_content(content: str) -> str:
    """Normalize memory content into a deterministic canonical form for deduplication."""
    if not content or not isinstance(content, str):
        return ""
    lowered = content.strip().lower()
    # Preserve common language tokens like C++ or C#, strip general punctuation
    cleaned = re.sub(r"[^\w\s+#]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def validate_memory_type(memory_type: str) -> str:
    """Validate and return normalized memory_type."""
    cleaned = (memory_type or "").strip().lower()
    if cleaned not in VALID_MEMORY_TYPES:
        raise InvalidMemoryTypeError(
            f"Unsupported memory_type '{memory_type}'. Must be one of: {', '.join(VALID_MEMORY_TYPES)}"
        )
    return cleaned


def clamp_importance(importance: Optional[int], default: int = 3) -> int:
    """Clamp memory importance score into inclusive range [1, 5]."""
    if importance is None:
        return default
    try:
        val = int(importance)
    except (TypeError, ValueError):
        return default
    return max(1, min(5, val))


def build_safe_fts5_query(query: str, operator: str = "AND") -> Optional[str]:
    """Sanitize arbitrary user text into a safe FTS5 MATCH expression."""
    raw_tokens = re.findall(r"[A-Za-z0-9_]+", query or "")
    if not raw_tokens:
        return None
    meaningful = [tok for tok in raw_tokens if tok.lower() not in _SEARCH_STOPWORDS]
    tokens = meaningful if meaningful else raw_tokens
    # Prefix-quoted tokens avoid FTS5 syntax errors with NOT/OR/AND/symbols
    quoted_terms = [f'"{tok}"*' for tok in tokens[:12]]
    if operator.upper() == "OR":
        return " OR ".join(quoted_terms)
    return " ".join(quoted_terms)


class LongTermMemoryService:
    """Local persistent memory service backed by SQLite + FTS5."""

    def __init__(self, sync_engine: Optional[Engine] = None):
        self._sync_engine = sync_engine or get_sync_engine()
        self._lock = threading.Lock()
        try:
            run_migrations_sync(self._sync_engine)
        except Exception as exc:
            logger.debug(f"Migration check in LongTermMemoryService skipped: {exc}")

    def _ensure_schema(self) -> None:
        run_migrations_sync(self._sync_engine)

    def _upsert_fts_entry(self, session: Session, record: MemoryRecord) -> None:
        """Keep memories_fts synchronized with MemoryRecord inside an active transaction."""
        try:
            if not is_fts5_available(self._sync_engine):
                return
            session.execute(
                text("DELETE FROM memories_fts WHERE memory_id = :mid"),
                {"mid": record.id},
            )
            session.execute(
                text(
                    "INSERT INTO memories_fts (memory_id, content, memory_type, profile_id) "
                    "VALUES (:mid, :content, :mtype, :pid)"
                ),
                {
                    "mid": record.id,
                    "content": record.content,
                    "mtype": record.memory_type,
                    "pid": record.profile_id,
                },
            )
        except Exception as fts_err:
            logger.warning(f"FTS5 sync skipped for memory '{record.id}': {fts_err}")

    def _delete_fts_entry(self, session: Session, memory_id: str) -> None:
        """Delete a memory entry from memories_fts inside an active transaction."""
        try:
            if not is_fts5_available(self._sync_engine):
                return
            session.execute(
                text("DELETE FROM memories_fts WHERE memory_id = :mid"),
                {"mid": memory_id},
            )
        except Exception as fts_err:
            logger.warning(f"FTS5 delete skipped for memory '{memory_id}': {fts_err}")

    def _validate_content(self, content: str) -> str:
        if not content or not isinstance(content, str) or not content.strip():
            raise ValueError("Memory content cannot be empty.")
        cleaned = re.sub(r"\s+", " ", content).strip()
        if len(cleaned) > 1000:
            cleaned = cleaned[:1000].strip()
        has_secret, reason = contains_obvious_secret(cleaned)
        if has_secret:
            raise MemoryPrivacyError(
                f"Memory content rejected for privacy protection (detected: {reason})."
            )
        return cleaned

    def create_memory(
        self,
        content: str,
        memory_type: str = "general_context",
        importance: int = 3,
        profile_id: str = "default",
        source_conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or deduplicate a persistent local memory item."""
        cleaned_content = self._validate_content(content)
        validated_type = validate_memory_type(memory_type)
        clamped_importance = clamp_importance(importance)
        clean_profile = (profile_id or "default").strip() or "default"
        normalized = normalize_memory_content(cleaned_content)

        safe_metadata = sanitize_dict_for_storage(metadata or {})
        if not isinstance(safe_metadata, dict):
            safe_metadata = {}

        now = datetime.now(timezone.utc)
        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                existing_stmt = select(MemoryRecord).where(
                    MemoryRecord.profile_id == clean_profile,
                    MemoryRecord.normalized_content == normalized,
                )
                existing = session.exec(existing_stmt).first()

                if existing is not None:
                    existing.updated_at = now
                    existing.last_accessed_at = now
                    existing.importance = max(existing.importance, clamped_importance)
                    if existing.memory_type == "general_context" and validated_type != "general_context":
                        existing.memory_type = validated_type
                    if source_conversation_id and not existing.source_conversation_id:
                        existing.source_conversation_id = source_conversation_id
                    if safe_metadata:
                        merged_meta = existing.get_metadata()
                        merged_meta.update(safe_metadata)
                        existing.metadata_json = json.dumps(merged_meta, default=str)
                    session.add(existing)
                    session.flush()
                    self._upsert_fts_entry(session, existing)
                    session.commit()
                    session.refresh(existing)
                    result = existing.to_dict()
                    result["deduplicated"] = True
                    return result

                record = MemoryRecord(
                    id=f"mem_{uuid.uuid4().hex[:12]}",
                    profile_id=clean_profile,
                    memory_type=validated_type,
                    content=cleaned_content,
                    normalized_content=normalized,
                    source_conversation_id=source_conversation_id,
                    importance=clamped_importance,
                    created_at=now,
                    updated_at=now,
                    last_accessed_at=now,
                    metadata_json=json.dumps(safe_metadata, default=str),
                )
                session.add(record)
                session.flush()
                self._upsert_fts_entry(session, record)
                session.commit()
                session.refresh(record)
                result = record.to_dict()
                result["deduplicated"] = False
                return result

    def get_memory(
        self,
        memory_id: str,
        touch: bool = True,
        profile_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a memory item by ID, optionally enforcing profile ownership."""
        if not memory_id or not isinstance(memory_id, str):
            return None
        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id.strip())
                if profile_id is not None:
                    clean_profile = (profile_id or "default").strip() or "default"
                    stmt = stmt.where(MemoryRecord.profile_id == clean_profile)
                record = session.exec(stmt).first()
                if record is None:
                    return None
                if touch:
                    record.last_accessed_at = datetime.now(timezone.utc)
                    session.add(record)
                    session.commit()
                    session.refresh(record)
                return record.to_dict()

    def list_memories(
        self,
        limit: int = 50,
        offset: int = 0,
        memory_type: Optional[str] = None,
        profile_id: str = "default",
    ) -> Dict[str, Any]:
        """List stored memories with pagination and optional memory_type filtering."""
        clean_profile = (profile_id or "default").strip() or "default"
        validated_type = validate_memory_type(memory_type) if memory_type else None
        safe_limit = max(1, min(200, int(limit)))
        safe_offset = max(0, int(offset))

        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                filters = [MemoryRecord.profile_id == clean_profile]
                if validated_type:
                    filters.append(MemoryRecord.memory_type == validated_type)

                count_stmt = select(func.count()).select_from(MemoryRecord).where(*filters)
                total = int(session.exec(count_stmt).one() or 0)

                list_stmt = (
                    select(MemoryRecord)
                    .where(*filters)
                    .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.importance.desc())
                    .offset(safe_offset)
                    .limit(safe_limit)
                )
                rows = list(session.exec(list_stmt).all())
                return {
                    "memories": [r.to_dict() for r in rows],
                    "total": total,
                    "limit": safe_limit,
                    "offset": safe_offset,
                }

    def _touch_records(self, session: Session, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        now = datetime.now(timezone.utc)
        for rec in records:
            rec.last_accessed_at = now
            session.add(rec)
        session.commit()

    def search_memories(
        self,
        query: str,
        limit: int = 10,
        memory_type: Optional[str] = None,
        profile_id: str = "default",
        match_mode: str = "auto",
        touch: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search local long-term memories using SQLite FTS5 with parameterized LIKE fallback."""
        clean_profile = (profile_id or "default").strip() or "default"
        validated_type = validate_memory_type(memory_type) if memory_type else None
        safe_limit = max(1, min(50, int(limit)))
        clean_query = (query or "").strip()

        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                if not clean_query:
                    filters = [MemoryRecord.profile_id == clean_profile]
                    if validated_type:
                        filters.append(MemoryRecord.memory_type == validated_type)
                    stmt = (
                        select(MemoryRecord)
                        .where(*filters)
                        .order_by(MemoryRecord.importance.desc(), MemoryRecord.updated_at.desc())
                        .limit(safe_limit)
                    )
                    rows = list(session.exec(stmt).all())
                    if touch:
                        self._touch_records(session, rows)
                    return [r.to_dict() for r in rows]

                matched_ids: List[str] = []
                use_fts = is_fts5_available(self._sync_engine)
                if use_fts:
                    fts_op = "OR" if match_mode == "any" else "AND"
                    fts_expr = build_safe_fts5_query(clean_query, operator=fts_op)
                    if fts_expr:
                        try:
                            sql = (
                                "SELECT f.memory_id "
                                "FROM memories_fts f "
                                "JOIN memoryrecord m ON m.id = f.memory_id "
                                "WHERE memories_fts MATCH :q AND m.profile_id = :pid "
                            )
                            params: Dict[str, Any] = {
                                "q": fts_expr,
                                "pid": clean_profile,
                                "lim": safe_limit,
                            }
                            if validated_type:
                                sql += "AND m.memory_type = :mtype "
                                params["mtype"] = validated_type
                            sql += "ORDER BY bm25(memories_fts), m.importance DESC, m.updated_at DESC LIMIT :lim"
                            rows = session.execute(text(sql), params).fetchall()
                            matched_ids = [str(r[0]) for r in rows]
                        except Exception as fts_err:
                            logger.debug(f"FTS5 query fallback triggered: {fts_err}")

                if matched_ids:
                    stmt = select(MemoryRecord).where(MemoryRecord.id.in_(matched_ids))
                    by_id = {rec.id: rec for rec in session.exec(stmt).all()}
                    ordered_records = [by_id[mid] for mid in matched_ids if mid in by_id]
                    if touch:
                        self._touch_records(session, ordered_records)
                    return [r.to_dict() for r in ordered_records]

                # Parameterized LIKE fallback (also handles partial substrings when FTS returns none)
                like_sql = (
                    "SELECT id FROM memoryrecord "
                    "WHERE profile_id = :pid AND LOWER(content) LIKE :pattern "
                )
                like_params: Dict[str, Any] = {
                    "pid": clean_profile,
                    "pattern": f"%{clean_query.lower()}%",
                    "lim": safe_limit,
                }
                if validated_type:
                    like_sql += "AND memory_type = :mtype "
                    like_params["mtype"] = validated_type
                like_sql += "ORDER BY importance DESC, updated_at DESC LIMIT :lim"
                like_rows = session.execute(text(like_sql), like_params).fetchall()
                fallback_ids = [str(r[0]) for r in like_rows]
                if not fallback_ids:
                    return []
                stmt = select(MemoryRecord).where(MemoryRecord.id.in_(fallback_ids))
                by_id = {rec.id: rec for rec in session.exec(stmt).all()}
                ordered_records = [by_id[mid] for mid in fallback_ids if mid in by_id]
                if touch:
                    self._touch_records(session, ordered_records)
                return [r.to_dict() for r in ordered_records]

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        memory_type: Optional[str] = None,
        importance: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        profile_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing memory item and keep memories_fts synchronized."""
        if not memory_id or not isinstance(memory_id, str):
            return None

        cleaned_content = self._validate_content(content) if content is not None else None
        validated_type = validate_memory_type(memory_type) if memory_type is not None else None

        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id.strip())
                if profile_id is not None:
                    clean_profile = (profile_id or "default").strip() or "default"
                    stmt = stmt.where(MemoryRecord.profile_id == clean_profile)
                record = session.exec(stmt).first()
                if record is None:
                    return None

                if cleaned_content is not None:
                    record.content = cleaned_content
                    record.normalized_content = normalize_memory_content(cleaned_content)
                if validated_type is not None:
                    record.memory_type = validated_type
                if importance is not None:
                    record.importance = clamp_importance(importance, record.importance)
                if metadata is not None:
                    safe_meta = sanitize_dict_for_storage(metadata)
                    if isinstance(safe_meta, dict):
                        record.metadata_json = json.dumps(safe_meta, default=str)

                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.flush()
                self._upsert_fts_entry(session, record)
                session.commit()
                session.refresh(record)
                return record.to_dict()

    def delete_memory(
        self,
        memory_id: str,
        profile_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory record and its FTS5 index row in one transaction."""
        if not memory_id or not isinstance(memory_id, str):
            return False

        with self._lock:
            self._ensure_schema()
            with Session(self._sync_engine) as session:
                stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id.strip())
                if profile_id is not None:
                    clean_profile = (profile_id or "default").strip() or "default"
                    stmt = stmt.where(MemoryRecord.profile_id == clean_profile)
                record = session.exec(stmt).first()
                if record is None:
                    return False
                self._delete_fts_entry(session, record.id)
                session.delete(record)
                session.commit()
                return True


_SHARED_MEMORY_SERVICE: Optional[LongTermMemoryService] = None


def get_shared_long_term_memory_service() -> LongTermMemoryService:
    """Return shared LongTermMemoryService singleton."""
    global _SHARED_MEMORY_SERVICE
    if _SHARED_MEMORY_SERVICE is None:
        _SHARED_MEMORY_SERVICE = LongTermMemoryService()
    return _SHARED_MEMORY_SERVICE


def reset_shared_long_term_memory_service() -> None:
    """Reset shared LongTermMemoryService singleton (used in tests)."""
    global _SHARED_MEMORY_SERVICE
    _SHARED_MEMORY_SERVICE = None
