"""
VoxPilot - SQLite Online Backup, Verification, and Recovery Manager (Stage D)

Provides consistent online SQLite snapshots (via `sqlite3.Connection.backup`),
manifest SHA-256 verification, encrypted DPAPI credential/config preservation,
non-overwrite guards, and explicit opt-in recovery procedures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.database.connection import init_db_sync, reset_db_connection
from backend.paths import (
    get_backups_dir,
    get_config_dir,
    get_credentials_dir,
    get_database_path,
)
from backend.version import get_app_version

logger = logging.getLogger(__name__)

_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9_\-]")
_CORE_TABLES = (
    "conversationrecord",
    "executionrecord",
    "executionsteprecord",
    "memoryrecord",
    "preferencerecord",
    "profilerecord",
    "profile_provider_configs",
    "automations",
    "automation_executions",
)


class BackupError(Exception):
    """Base error for VoxPilot backup and recovery operations."""


class BackupAlreadyExistsError(BackupError):
    """Raised when a backup target already exists and overwrite is disallowed."""


class BackupVerificationError(BackupError):
    """Raised when a backup manifest, checksum, or SQLite integrity check fails."""


class BackupRestoreConfirmationError(BackupError):
    """Raised when restore is invoked without explicit confirm_overwrite=True."""


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_label(label: Optional[str]) -> str:
    if not label or not str(label).strip():
        return uuid.uuid4().hex[:8]
    cleaned = _SAFE_LABEL_RE.sub("_", str(label).strip()).strip("_")
    return cleaned[:40] or uuid.uuid4().hex[:8]


def _collect_table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    existing = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for tbl in _CORE_TABLES:
        if tbl in existing:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            counts[tbl] = int(row[0]) if row else 0
    return counts


class DatabaseBackupManager:
    """Manages SQLite-consistent snapshots and explicit non-automatic recovery."""

    MANIFEST_FILENAME = "backup_manifest.json"
    DB_FILENAME = "voxpilot.db"

    def __init__(
        self,
        db_path: Optional[Path] = None,
        backups_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None,
        credentials_dir: Optional[Path] = None,
    ) -> None:
        self.db_path = Path(db_path).resolve() if db_path is not None else get_database_path()
        self.backups_dir = Path(backups_dir).resolve() if backups_dir is not None else get_backups_dir()
        self.config_dir = Path(config_dir).resolve() if config_dir is not None else get_config_dir()
        self.credentials_dir = (
            Path(credentials_dir).resolve() if credentials_dir is not None else get_credentials_dir()
        )

    def _allocate_backup_dir(self, label: Optional[str], target_dir: Optional[Path], allow_overwrite: bool) -> Path:
        if target_dir is not None:
            resolved = Path(target_dir).resolve()
            if resolved.exists() and any(resolved.iterdir()) and not allow_overwrite:
                raise BackupAlreadyExistsError(
                    f"Backup directory '{resolved}' already exists and is non-empty."
                )
            resolved.mkdir(parents=True, exist_ok=True)
            return resolved

        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_label = _sanitize_label(label)
        candidate = (self.backups_dir / f"voxpilot_backup_{stamp}_{safe_label}").resolve()
        suffix = 1
        while candidate.exists():
            candidate = (self.backups_dir / f"voxpilot_backup_{stamp}_{safe_label}_{suffix}").resolve()
            suffix += 1
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    @staticmethod
    def _copy_filtered_tree(
        src_root: Path,
        dest_root: Path,
        allowed_suffixes: tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        copied: List[Dict[str, Any]] = []
        if not src_root.is_dir():
            return copied

        for item in sorted(src_root.rglob("*")):
            if not item.is_file():
                continue
            name_lower = item.name.lower()
            if name_lower.endswith(".tmp") or not any(name_lower.endswith(ext) for ext in allowed_suffixes):
                continue
            rel = item.relative_to(src_root)
            dest = (dest_root / rel).resolve()
            if not dest.is_relative_to(dest_root.resolve()):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f"{dest.name}.tmp")
            shutil.copy2(item, tmp_dest)
            os.replace(tmp_dest, dest)
            copied.append(
                {
                    "relative_path": rel.as_posix(),
                    "size_bytes": dest.stat().st_size,
                    "sha256": _sha256_file(dest),
                }
            )
        return copied

    def create_backup(
        self,
        label: Optional[str] = None,
        target_dir: Optional[Path] = None,
        include_config: bool = True,
        include_credentials: bool = True,
        allow_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Create a consistent SQLite Online Backup snapshot with manifest verification."""
        if not self.db_path.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            init_db_sync(f"sqlite:///{self.db_path}")

        snapshot_dir = self._allocate_backup_dir(label, target_dir, allow_overwrite)
        dest_db_path = snapshot_dir / self.DB_FILENAME

        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        try:
            src_conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            dst_conn = sqlite3.connect(str(dest_db_path), timeout=10.0)
            src_conn.backup(dst_conn)
            dst_conn.commit()
            integrity_row = dst_conn.execute("PRAGMA integrity_check;").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "unknown"
            if integrity.lower() != "ok":
                raise BackupVerificationError(f"Backed-up database failed PRAGMA integrity_check: {integrity}")
            table_counts = _collect_table_counts(dst_conn)
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()

        config_files: List[Dict[str, Any]] = []
        if include_config:
            config_files = self._copy_filtered_tree(
                self.config_dir,
                snapshot_dir / "config",
                (".json",),
            )

        credential_files: List[Dict[str, Any]] = []
        if include_credentials:
            credential_files = self._copy_filtered_tree(
                self.credentials_dir,
                snapshot_dir / "credentials",
                (".dpapi.json", ".json"),
            )

        manifest: Dict[str, Any] = {
            "manifest_version": 1,
            "app_version": get_app_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backup_dir": str(snapshot_dir),
            "source_db_path": str(self.db_path),
            "database": {
                "filename": self.DB_FILENAME,
                "size_bytes": dest_db_path.stat().st_size,
                "sha256": _sha256_file(dest_db_path),
                "integrity_check": "ok",
                "table_counts": table_counts,
            },
            "config_files": config_files,
            "credential_files": credential_files,
        }

        manifest_path = snapshot_dir / self.MANIFEST_FILENAME
        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp_manifest, manifest_path)
        logger.info("Created verified SQLite backup at '%s' (%d bytes).", snapshot_dir, dest_db_path.stat().st_size)
        return manifest

    def verify_backup(self, backup_dir: Path) -> Dict[str, Any]:
        """Verify backup manifest checksums and SQLite PRAGMA integrity_check."""
        resolved = Path(backup_dir).resolve()
        manifest_path = resolved / self.MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise BackupVerificationError(f"Backup manifest missing at '{manifest_path}'.")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise BackupVerificationError(f"Invalid backup manifest JSON: {exc}") from exc

        db_info = manifest.get("database") or {}
        db_file = resolved / str(db_info.get("filename") or self.DB_FILENAME)
        if not db_file.is_file():
            raise BackupVerificationError(f"Backup database file missing at '{db_file}'.")

        actual_hash = _sha256_file(db_file)
        expected_hash = str(db_info.get("sha256") or "")
        if not expected_hash or actual_hash != expected_hash:
            raise BackupVerificationError("Backup database SHA-256 checksum mismatch.")

        for section, subdir in (("config_files", "config"), ("credential_files", "credentials")):
            base = (resolved / subdir).resolve()
            for entry in manifest.get(section) or []:
                rel = str(entry.get("relative_path") or "")
                target = (base / rel).resolve()
                if not rel or not target.is_relative_to(base) or not target.is_file():
                    raise BackupVerificationError(f"Backed-up file missing or invalid in {section}: '{rel}'.")
                if _sha256_file(target) != str(entry.get("sha256") or ""):
                    raise BackupVerificationError(f"Checksum mismatch for backed-up file '{rel}'.")

        check_conn: Optional[sqlite3.Connection] = None
        try:
            check_conn = sqlite3.connect(str(db_file), timeout=10.0)
            row = check_conn.execute("PRAGMA integrity_check;").fetchone()
            integrity = str(row[0]) if row else "unknown"
            if integrity.lower() != "ok":
                raise BackupVerificationError(f"Backup SQLite PRAGMA integrity_check failed: {integrity}")
        finally:
            if check_conn is not None:
                check_conn.close()

        return manifest

    def list_backups(self) -> List[Dict[str, Any]]:
        """List verified manifests available inside self.backups_dir ordered newest-first."""
        if not self.backups_dir.is_dir():
            return []
        manifests: List[Dict[str, Any]] = []
        for child in self.backups_dir.iterdir():
            if not child.is_dir():
                continue
            manifest_path = child / self.MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                data["backup_dir"] = str(child.resolve())
                manifests.append(data)
            except Exception:
                continue
        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return manifests

    def restore_backup(
        self,
        backup_dir: Path,
        confirm_overwrite: bool = False,
        restore_config: bool = True,
        restore_credentials: bool = True,
        create_pre_restore_snapshot: bool = True,
    ) -> Dict[str, Any]:
        """Restore a verified backup snapshot strictly when confirm_overwrite=True."""
        if confirm_overwrite is not True:
            raise BackupRestoreConfirmationError(
                "Restore refused: explicit confirm_overwrite=True is required to overwrite user data."
            )

        resolved_backup = Path(backup_dir).resolve()
        manifest = self.verify_backup(resolved_backup)

        pre_restore_manifest: Optional[Dict[str, Any]] = None
        if create_pre_restore_snapshot and self.db_path.exists():
            pre_restore_manifest = self.create_backup(
                label="pre_restore_safety",
                include_config=restore_config,
                include_credentials=restore_credentials,
            )

        reset_db_connection()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        source_db = resolved_backup / str((manifest.get("database") or {}).get("filename") or self.DB_FILENAME)
        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        try:
            src_conn = sqlite3.connect(str(source_db), timeout=10.0)
            dst_conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()

        init_db_sync(f"sqlite:///{self.db_path}")

        restored_config = 0
        if restore_config:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            cfg_items = self._copy_filtered_tree(resolved_backup / "config", self.config_dir, (".json",))
            restored_config = len(cfg_items)

        restored_credentials = 0
        if restore_credentials:
            self.credentials_dir.mkdir(parents=True, exist_ok=True)
            cred_items = self._copy_filtered_tree(
                resolved_backup / "credentials",
                self.credentials_dir,
                (".dpapi.json", ".json"),
            )
            restored_credentials = len(cred_items)

        return {
            "restored": True,
            "backup_dir": str(resolved_backup),
            "target_db_path": str(self.db_path),
            "pre_restore_backup_dir": pre_restore_manifest["backup_dir"] if pre_restore_manifest else None,
            "restored_config_files": restored_config,
            "restored_credential_files": restored_credentials,
        }
