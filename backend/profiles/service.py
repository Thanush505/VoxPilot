"""
ProfileService — Local-first multi-profile management for VoxPilot.

Manages local application profiles (`profilerecord`), active profile resolution,
default profile protection, and safe profile switching without cloud sync.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.database.connection import get_sync_engine, is_fts5_available, run_migrations_sync
from backend.database.models import (
    VALID_ACCENT_COLORS,
    VALID_PROFILE_AVATARS,
    VALID_THEME_MODES,
    ConversationRecord,
    ExecutionRecord,
    ExecutionStepRecord,
    MemoryRecord,
    PreferenceRecord,
    ProfileRecord,
    contains_obvious_secret,
)

logger = logging.getLogger(__name__)

_UNSET = object()


class InvalidProfileError(ValueError):
    """Raised when profile name, avatar, or description is invalid."""


class ProfilePrivacyError(ValueError):
    """Raised when profile fields contain secret-like credentials."""


class DuplicateProfileError(ValueError):
    """Raised when creating/renaming a profile to an existing name."""


class DefaultProfileDeletionError(ValueError):
    """Raised when attempting to delete the protected default profile."""


class ProfileNotFoundError(ValueError):
    """Raised when a referenced profile ID does not exist."""


def normalize_profile_name(name: str) -> str:
    """Normalize profile display name for case/whitespace-insensitive uniqueness."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    return cleaned


class ProfileService:
    """Thread-safe local profile management service backed by SQLite."""

    def __init__(self, sync_engine: Optional[Engine] = None) -> None:
        self._sync_engine = sync_engine or get_sync_engine()
        self._lock = threading.RLock()
        run_migrations_sync(self._sync_engine)

    @staticmethod
    def _validate_name(name: str) -> str:
        cleaned = re.sub(r"\s+", " ", (name or "").strip())
        if not cleaned:
            raise InvalidProfileError("Profile name cannot be empty.")
        if len(cleaned) > 64:
            raise InvalidProfileError("Profile name cannot exceed 64 characters.")
        has_secret, reason = contains_obvious_secret(cleaned)
        if has_secret:
            raise ProfilePrivacyError(
                f"Refusing to store profile name: detected potential secret ({reason})."
            )
        return cleaned

    @staticmethod
    def _validate_description(description: Optional[str]) -> Optional[str]:
        if description is None:
            return None
        cleaned = description.strip()
        if not cleaned:
            return None
        if len(cleaned) > 250:
            raise InvalidProfileError("Profile description cannot exceed 250 characters.")
        has_secret, reason = contains_obvious_secret(cleaned)
        if has_secret:
            raise ProfilePrivacyError(
                f"Refusing to store profile description: detected potential secret ({reason})."
            )
        return cleaned

    @staticmethod
    def _validate_avatar(avatar: Optional[str]) -> str:
        cleaned = (avatar or "user").strip().lower()
        if cleaned not in VALID_PROFILE_AVATARS:
            raise InvalidProfileError(
                f"Invalid avatar '{avatar}'. Allowed: {', '.join(VALID_PROFILE_AVATARS)}."
            )
        return cleaned

    @staticmethod
    def _validate_theme_mode(theme_mode: Optional[str]) -> str:
        cleaned = (theme_mode or "dark").strip().lower()
        if cleaned not in VALID_THEME_MODES:
            raise InvalidProfileError(
                f"Invalid theme mode '{theme_mode}'. Allowed: {', '.join(VALID_THEME_MODES)}."
            )
        return cleaned

    @staticmethod
    def _validate_accent_color(accent_color: Optional[str]) -> str:
        cleaned = (accent_color or "blue").strip().lower()
        if cleaned not in VALID_ACCENT_COLORS:
            raise InvalidProfileError(
                f"Invalid accent color '{accent_color}'. Allowed: {', '.join(VALID_ACCENT_COLORS)}."
            )
        return cleaned

    def _ensure_default_and_active(self, session: Session) -> ProfileRecord:
        default_prof = session.get(ProfileRecord, "default")
        now = datetime.now(timezone.utc)
        if default_prof is None:
            default_prof = ProfileRecord(
                id="default",
                name="Default",
                normalized_name="default",
                description="Default local VoxPilot profile",
                avatar="user",
                is_active=True,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
            session.add(default_prof)
            session.commit()
            session.refresh(default_prof)

        active = session.exec(
            select(ProfileRecord).where(ProfileRecord.is_active == True)  # noqa: E712
        ).first()
        if active is None:
            default_prof.is_active = True
            default_prof.updated_at = now
            session.add(default_prof)
            session.commit()
            session.refresh(default_prof)
            return default_prof
        return active

    def list_profiles(self) -> List[Dict[str, Any]]:
        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                rows = list(
                    session.exec(
                        select(ProfileRecord).order_by(
                            ProfileRecord.is_default.desc(),
                            ProfileRecord.created_at.asc(),
                        )
                    ).all()
                )
                return [r.to_dict() for r in rows]

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        if not profile_id or not isinstance(profile_id, str):
            return None
        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                record = session.get(ProfileRecord, profile_id.strip())
                return record.to_dict() if record else None

    def get_active_profile(self) -> Dict[str, Any]:
        with self._lock:
            with Session(self._sync_engine) as session:
                active = self._ensure_default_and_active(session)
                return active.to_dict()

    def get_active_profile_id(self) -> str:
        return self.get_active_profile()["id"]

    def profile_exists(self, profile_id: str) -> bool:
        if not profile_id or not isinstance(profile_id, str):
            return False
        with self._lock:
            with Session(self._sync_engine) as session:
                return session.get(ProfileRecord, profile_id.strip()) is not None

    def create_profile(
        self,
        *,
        name: str,
        description: Optional[str] = None,
        avatar: str = "user",
        theme_mode: str = "dark",
        accent_color: str = "blue",
        activate: bool = False,
    ) -> Dict[str, Any]:
        clean_name = self._validate_name(name)
        norm_name = normalize_profile_name(clean_name)
        clean_desc = self._validate_description(description)
        clean_avatar = self._validate_avatar(avatar)
        clean_theme_mode = self._validate_theme_mode(theme_mode)
        clean_accent = self._validate_accent_color(accent_color)
        now = datetime.now(timezone.utc)

        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                existing = session.exec(
                    select(ProfileRecord).where(
                        ProfileRecord.normalized_name == norm_name
                    )
                ).first()
                if existing is not None:
                    raise DuplicateProfileError(
                        f"A profile named '{clean_name}' already exists."
                    )

                if activate:
                    all_active = session.exec(
                        select(ProfileRecord).where(ProfileRecord.is_active == True)  # noqa: E712
                    ).all()
                    for prof in all_active:
                        prof.is_active = False
                        session.add(prof)

                record = ProfileRecord(
                    name=clean_name,
                    normalized_name=norm_name,
                    description=clean_desc,
                    avatar=clean_avatar,
                    theme_mode=clean_theme_mode,
                    accent_color=clean_accent,
                    is_active=bool(activate),
                    is_default=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                return record.to_dict()

    def update_profile(
        self,
        profile_id: str,
        *,
        name: Optional[str] = None,
        description: Any = _UNSET,
        avatar: Optional[str] = None,
        theme_mode: Optional[str] = None,
        accent_color: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not profile_id or not isinstance(profile_id, str):
            return None
        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                record = session.get(ProfileRecord, profile_id.strip())
                if record is None:
                    return None

                if name is not None:
                    clean_name = self._validate_name(name)
                    norm_name = normalize_profile_name(clean_name)
                    conflict = session.exec(
                        select(ProfileRecord).where(
                            ProfileRecord.normalized_name == norm_name,
                            ProfileRecord.id != record.id,
                        )
                    ).first()
                    if conflict is not None:
                        raise DuplicateProfileError(
                            f"A profile named '{clean_name}' already exists."
                        )
                    record.name = clean_name
                    record.normalized_name = norm_name

                if description is not _UNSET:
                    record.description = self._validate_description(description)

                if avatar is not None:
                    record.avatar = self._validate_avatar(avatar)

                if theme_mode is not None:
                    record.theme_mode = self._validate_theme_mode(theme_mode)

                if accent_color is not None:
                    record.accent_color = self._validate_accent_color(accent_color)

                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()
                session.refresh(record)
                return record.to_dict()

    def activate_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        if not profile_id or not isinstance(profile_id, str):
            return None
        clean_id = profile_id.strip()
        now = datetime.now(timezone.utc)
        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                target = session.get(ProfileRecord, clean_id)
                if target is None:
                    return None

                all_profs = session.exec(select(ProfileRecord)).all()
                for prof in all_profs:
                    should_active = prof.id == target.id
                    if bool(prof.is_active) != should_active:
                        prof.is_active = should_active
                        prof.updated_at = now
                        session.add(prof)

                session.commit()
                session.refresh(target)
                return target.to_dict()

    def delete_profile(self, profile_id: str, cascade_data: bool = True) -> bool:
        if not profile_id or not isinstance(profile_id, str):
            return False
        clean_id = profile_id.strip()
        now = datetime.now(timezone.utc)
        with self._lock:
            with Session(self._sync_engine) as session:
                self._ensure_default_and_active(session)
                target = session.get(ProfileRecord, clean_id)
                if target is None:
                    return False

                if target.is_default or target.id == "default":
                    raise DefaultProfileDeletionError(
                        "The default profile cannot be deleted."
                    )

                if target.is_active:
                    default_prof = session.get(ProfileRecord, "default")
                    if default_prof is not None:
                        default_prof.is_active = True
                        default_prof.updated_at = now
                        session.add(default_prof)

                if cascade_data:
                    prefs = session.exec(
                        select(PreferenceRecord).where(
                            PreferenceRecord.profile_id == clean_id
                        )
                    ).all()
                    for p in prefs:
                        session.delete(p)

                    mems = session.exec(
                        select(MemoryRecord).where(MemoryRecord.profile_id == clean_id)
                    ).all()
                    fts_ok = is_fts5_available(self._sync_engine)
                    for m in mems:
                        if fts_ok:
                            session.execute(
                                text("DELETE FROM memories_fts WHERE memory_id = :mid"),
                                {"mid": m.id},
                            )
                        session.delete(m)

                    convs = session.exec(
                        select(ConversationRecord).where(
                            ConversationRecord.profile_id == clean_id
                        )
                    ).all()
                    for c in convs:
                        execs = session.exec(
                            select(ExecutionRecord).where(
                                ExecutionRecord.conversation_id == c.conversation_id
                            )
                        ).all()
                        for ex in execs:
                            steps = session.exec(
                                select(ExecutionStepRecord).where(
                                    ExecutionStepRecord.execution_id == ex.execution_id
                                )
                            ).all()
                            for st in steps:
                                session.delete(st)
                            session.delete(ex)
                        session.delete(c)

                session.delete(target)
                session.commit()
                return True


_shared_profile_service: Optional[ProfileService] = None


def get_shared_profile_service() -> ProfileService:
    """Return a shared ProfileService singleton bound to the default SQLite engine."""
    global _shared_profile_service
    if _shared_profile_service is None:
        _shared_profile_service = ProfileService()
    return _shared_profile_service


def reset_shared_profile_service() -> None:
    """Reset the shared ProfileService singleton (useful for tests)."""
    global _shared_profile_service
    _shared_profile_service = None
