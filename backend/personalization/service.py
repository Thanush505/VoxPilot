"""
PersonalizationService — Local-first explicit user preference management.

Stores structured user preferences in SQLite (`preferencerecord`), separate from
automatically extracted `MemoryRecord` items, and builds compact,
relevance-filtered personalization context for `VoxPilotAgent`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.engine import Engine
from sqlmodel import Session, func, select

from backend.database.connection import get_sync_engine, run_migrations_sync
from backend.database.models import (
    VALID_PREFERENCE_CATEGORIES,
    PreferenceRecord,
    contains_obvious_secret,
    scrub_text_secrets,
    serialize_sanitized_json,
)

logger = logging.getLogger(__name__)

_UNSET = object()

ALWAYS_ACTIVE_CATEGORIES = frozenset(
    {"communication", "response_format", "ai_behavior"}
)
CONTEXTUAL_CATEGORIES = frozenset({"technical", "workflow", "interface"})

_CATEGORY_DOMAIN_HINTS: Dict[str, Set[str]] = {
    "technical": {
        "code", "coding", "python", "typescript", "javascript", "react", "sql",
        "api", "debug", "error", "test", "pytest", "git", "build", "script",
        "function", "class", "backend", "frontend", "database", "file", "config",
    },
    "workflow": {
        "plan", "step", "steps", "task", "workflow", "automate", "open",
        "launch", "run", "execute", "command", "confirm", "folder", "app",
        "application", "process", "schedule",
    },
    "interface": {
        "ui", "interface", "layout", "view", "dashboard", "settings", "voice",
        "speech", "audio", "display", "panel", "card", "table", "chart",
    },
}


class PreferencePrivacyError(ValueError):
    """Raised when a preference contains secret-like data."""


class InvalidPreferenceCategoryError(ValueError):
    """Raised when a preference category is not in VALID_PREFERENCE_CATEGORIES."""


class InvalidPreferenceValueError(ValueError):
    """Raised when a preference key, value, or priority is invalid."""


class DuplicatePreferenceError(ValueError):
    """Raised when creating a duplicate (profile_id, category, key) preference."""


def normalize_preference_key(key: str) -> str:
    """Normalize a preference key for case/whitespace/hyphen-insensitive uniqueness."""
    cleaned = re.sub(r"[\s\-]+", "_", (key or "").strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def _tokenize(text: str) -> Set[str]:
    return {
        tok
        for tok in re.findall(r"[a-z0-9_]+", (text or "").lower())
        if len(tok) >= 2
    }


class PersonalizationService:
    """Service for CRUD and prompt-context generation of explicit user preferences."""

    def __init__(self, sync_engine: Optional[Engine] = None) -> None:
        self._sync_engine = sync_engine or get_sync_engine()
        run_migrations_sync(self._sync_engine)

    @staticmethod
    def validate_category(category: str) -> str:
        cat = (category or "").strip().lower()
        if cat not in VALID_PREFERENCE_CATEGORIES:
            raise InvalidPreferenceCategoryError(
                f"Invalid preference category '{category}'. "
                f"Allowed categories: {', '.join(VALID_PREFERENCE_CATEGORIES)}."
            )
        return cat

    @staticmethod
    def _validate_text_field(
        field_name: str,
        value: Optional[str],
        *,
        required: bool = False,
        max_len: int = 500,
    ) -> Optional[str]:
        if value is None:
            if required:
                raise InvalidPreferenceValueError(f"{field_name} cannot be empty.")
            return None
        cleaned = value.strip()
        if required and not cleaned:
            raise InvalidPreferenceValueError(f"{field_name} cannot be empty.")
        if len(cleaned) > max_len:
            raise InvalidPreferenceValueError(
                f"{field_name} exceeds maximum length of {max_len} characters."
            )
        if cleaned:
            has_secret, secret_kind = contains_obvious_secret(cleaned)
            if has_secret:
                raise PreferencePrivacyError(
                    f"Refusing to store {field_name}: detected potential secret ({secret_kind})."
                )
        return cleaned

    @staticmethod
    def _validate_priority(priority: int) -> int:
        try:
            p = int(priority)
        except (TypeError, ValueError) as exc:
            raise InvalidPreferenceValueError("Priority must be an integer between 1 and 5.") from exc
        if p < 1 or p > 5:
            raise InvalidPreferenceValueError("Priority must be between 1 and 5.")
        return p

    def create_preference(
        self,
        *,
        key: str,
        value: str,
        category: str = "communication",
        profile_id: str = "default",
        description: Optional[str] = None,
        enabled: bool = True,
        priority: int = 3,
        metadata: Optional[Dict[str, Any]] = None,
        upsert: bool = False,
    ) -> Dict[str, Any]:
        cat = self.validate_category(category)
        clean_key = self._validate_text_field("key", key, required=True, max_len=100) or ""
        clean_value = self._validate_text_field("value", value, required=True, max_len=500) or ""
        clean_desc = self._validate_text_field("description", description, required=False, max_len=300)
        clean_priority = self._validate_priority(priority)
        norm_key = normalize_preference_key(clean_key)
        if not norm_key:
            raise InvalidPreferenceValueError("key must contain at least one alphanumeric character.")
        clean_profile = (profile_id or "default").strip() or "default"
        safe_meta_json = serialize_sanitized_json(metadata or {})

        now = datetime.now(timezone.utc)
        with Session(self._sync_engine) as session:
            existing = session.exec(
                select(PreferenceRecord).where(
                    PreferenceRecord.profile_id == clean_profile,
                    PreferenceRecord.category == cat,
                    PreferenceRecord.normalized_key == norm_key,
                )
            ).first()

            if existing is not None:
                if not upsert:
                    raise DuplicatePreferenceError(
                        f"Preference '{clean_key}' already exists in category '{cat}'."
                    )
                existing.key = clean_key
                existing.value = clean_value
                existing.description = clean_desc
                existing.enabled = bool(enabled)
                existing.priority = clean_priority
                existing.metadata_json = safe_meta_json
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing.to_dict()

            record = PreferenceRecord(
                profile_id=clean_profile,
                category=cat,
                key=clean_key,
                normalized_key=norm_key,
                value=clean_value,
                description=clean_desc,
                enabled=bool(enabled),
                priority=clean_priority,
                created_at=now,
                updated_at=now,
                metadata_json=safe_meta_json,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record.to_dict()

    def get_preference(
        self,
        preference_id: str,
        profile_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with Session(self._sync_engine) as session:
            record = session.get(PreferenceRecord, preference_id)
            if record is None:
                return None
            if profile_id is not None:
                clean_profile = (profile_id or "default").strip() or "default"
                if record.profile_id != clean_profile:
                    return None
            return record.to_dict()

    def list_preferences(
        self,
        *,
        profile_id: str = "default",
        category: Optional[str] = None,
        enabled_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clean_profile = (profile_id or "default").strip() or "default"
        stmt = select(PreferenceRecord).where(PreferenceRecord.profile_id == clean_profile)
        if category is not None:
            stmt = stmt.where(PreferenceRecord.category == self.validate_category(category))
        if enabled_only:
            stmt = stmt.where(PreferenceRecord.enabled == True)  # noqa: E712
        stmt = (
            stmt.order_by(
                PreferenceRecord.priority.desc(),
                PreferenceRecord.updated_at.desc(),
            )
            .offset(max(0, offset))
            .limit(max(1, min(limit, 500)))
        )
        with Session(self._sync_engine) as session:
            rows = session.exec(stmt).all()
            return [row.to_dict() for row in rows]

    def count_preferences(
        self,
        *,
        profile_id: str = "default",
        category: Optional[str] = None,
        enabled_only: bool = False,
    ) -> int:
        clean_profile = (profile_id or "default").strip() or "default"
        stmt = select(func.count()).select_from(PreferenceRecord).where(
            PreferenceRecord.profile_id == clean_profile
        )
        if category is not None:
            stmt = stmt.where(PreferenceRecord.category == self.validate_category(category))
        if enabled_only:
            stmt = stmt.where(PreferenceRecord.enabled == True)  # noqa: E712
        with Session(self._sync_engine) as session:
            return int(session.exec(stmt).one() or 0)

    def update_preference(
        self,
        preference_id: str,
        *,
        category: Optional[str] = None,
        key: Optional[str] = None,
        value: Optional[str] = None,
        description: Any = _UNSET,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        profile_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with Session(self._sync_engine) as session:
            record = session.get(PreferenceRecord, preference_id)
            if record is None:
                return None
            if profile_id is not None:
                clean_profile = (profile_id or "default").strip() or "default"
                if record.profile_id != clean_profile:
                    return None

            new_category = self.validate_category(category) if category is not None else record.category
            new_key = (
                self._validate_text_field("key", key, required=True, max_len=100)
                if key is not None
                else record.key
            ) or record.key
            new_norm_key = normalize_preference_key(new_key)
            if not new_norm_key:
                raise InvalidPreferenceValueError("key must contain at least one alphanumeric character.")

            if new_category != record.category or new_norm_key != record.normalized_key:
                conflict = session.exec(
                    select(PreferenceRecord).where(
                        PreferenceRecord.profile_id == record.profile_id,
                        PreferenceRecord.category == new_category,
                        PreferenceRecord.normalized_key == new_norm_key,
                        PreferenceRecord.id != record.id,
                    )
                ).first()
                if conflict is not None:
                    raise DuplicatePreferenceError(
                        f"Preference '{new_key}' already exists in category '{new_category}'."
                    )

            record.category = new_category
            record.key = new_key
            record.normalized_key = new_norm_key

            if value is not None:
                record.value = (
                    self._validate_text_field("value", value, required=True, max_len=500)
                    or record.value
                )
            if description is not _UNSET:
                record.description = self._validate_text_field(
                    "description", description, required=False, max_len=300
                )
            if enabled is not None:
                record.enabled = bool(enabled)
            if priority is not None:
                record.priority = self._validate_priority(priority)
            if metadata is not None:
                record.metadata_json = serialize_sanitized_json(metadata)

            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record.to_dict()

    def delete_preference(
        self,
        preference_id: str,
        profile_id: Optional[str] = None,
    ) -> bool:
        with Session(self._sync_engine) as session:
            record = session.get(PreferenceRecord, preference_id)
            if record is None:
                return False
            if profile_id is not None:
                clean_profile = (profile_id or "default").strip() or "default"
                if record.profile_id != clean_profile:
                    return False
            session.delete(record)
            session.commit()
            return True

    def build_personalization_context(
        self,
        *,
        user_input: str = "",
        intent: Optional[str] = None,
        profile_id: str = "default",
        max_items: int = 8,
        max_chars: int = 1200,
    ) -> Dict[str, Any]:
        """Build a compact, relevance-filtered personalization context for the AI agent.

        Only enabled preferences are considered. Core behavioral/style categories
        (`communication`, `response_format`, `ai_behavior`) are always included.
        Domain categories (`technical`, `workflow`, `interface`) are filtered by
        relevance to the current input/intent so prompts stay concise.
        """
        active_prefs = self.list_preferences(
            profile_id=profile_id,
            enabled_only=True,
            limit=100,
        )
        if not active_prefs:
            return {
                "preferences": [],
                "summary_text": "",
                "count": 0,
                "total_enabled": 0,
            }

        query_tokens = _tokenize(f"{user_input} {intent or ''}")
        always_items: List[Dict[str, Any]] = []
        contextual_scored: List[tuple[int, Dict[str, Any]]] = []

        for pref in active_prefs:
            cat = pref.get("category", "")
            if cat in ALWAYS_ACTIVE_CATEGORIES:
                always_items.append(pref)
                continue

            if not query_tokens:
                contextual_scored.append((int(pref.get("priority", 3)), pref))
                continue

            pref_tokens = _tokenize(
                f"{pref.get('key', '')} {pref.get('value', '')} "
                f"{pref.get('description') or ''} {cat}"
            )
            overlap = len(query_tokens & pref_tokens)
            domain_hits = len(query_tokens & _CATEGORY_DOMAIN_HINTS.get(cat, set()))
            if overlap > 0 or domain_hits > 0:
                score = (overlap * 3) + (domain_hits * 2) + int(pref.get("priority", 3))
                contextual_scored.append((score, pref))

        contextual_scored.sort(key=lambda item: item[0], reverse=True)

        selected: List[Dict[str, Any]] = []
        for pref in always_items:
            if len(selected) >= max_items:
                break
            selected.append(pref)
        for _, pref in contextual_scored:
            if len(selected) >= max_items:
                break
            selected.append(pref)

        lines: List[str] = []
        total_len = 0
        included_prefs: List[Dict[str, Any]] = []
        for pref in selected:
            safe_val = scrub_text_secrets(str(pref.get("value", "")))
            safe_key = scrub_text_secrets(str(pref.get("key", "")))
            line = f"- [{pref['category']}] {safe_key}: {safe_val}"
            desc = pref.get("description")
            if desc and len(desc) <= 100:
                line += f" ({scrub_text_secrets(str(desc))})"
            if total_len + len(line) + 1 > max_chars and included_prefs:
                break
            lines.append(line)
            included_prefs.append(pref)
            total_len += len(line) + 1

        return {
            "preferences": included_prefs,
            "summary_text": "\n".join(lines),
            "count": len(included_prefs),
            "total_enabled": len(active_prefs),
        }


_shared_personalization_service: Optional[PersonalizationService] = None


def get_shared_personalization_service() -> PersonalizationService:
    """Return a shared PersonalizationService bound to the default SQLite engine."""
    global _shared_personalization_service
    if _shared_personalization_service is None:
        _shared_personalization_service = PersonalizationService()
    return _shared_personalization_service


def reset_shared_personalization_service() -> None:
    """Reset the shared PersonalizationService singleton (useful for tests)."""
    global _shared_personalization_service
    _shared_personalization_service = None

