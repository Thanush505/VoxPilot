"""
VoxPilot - Audit Trail Export Subsystem
Provides secure querying, sensitive credential scrubbing, and export of persistent
agent execution histories and step records to structured JSON and CSV formats.
Enforces destination directory containment, path traversal prevention, and overwrite protection.
"""

from __future__ import annotations

import csv
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from sqlmodel import Session, select
from sqlalchemy.engine import Engine

from backend.paths import get_export_dir
from backend.database.connection import get_sync_engine
from backend.database.models import ExecutionRecord, ExecutionStepRecord

logger = logging.getLogger(__name__)

# Keys whose values must be scrubbed / redacted in audit exports
SENSITIVE_AUDIT_KEYS = {
    "token",
    "confirmation_token",
    "password",
    "secret",
    "api_key",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "private_key",
}


def scrub_sensitive_data(data: Any, max_string_len: int = 1000) -> Any:
    """Recursively redact passwords, confirmation tokens, secrets, and auth credentials.

    Replaces sensitive key values with '[REDACTED]' and ensures no unmasked tokens escape to exports.
    """
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_AUDIT_KEYS):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = scrub_sensitive_data(v, max_string_len)
        return cleaned
    elif isinstance(data, list):
        return [scrub_sensitive_data(item, max_string_len) for item in data]
    elif isinstance(data, str):
        if len(data) > max_string_len:
            return data[:max_string_len] + "... [truncated]"
        return data
    return data


def parse_filter_datetime(val: Any) -> Optional[datetime]:
    """Parse various datetime representations into a timezone-aware UTC datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(val, str):
        clean = val.strip()
        # Try fromisoformat
        try:
            dt = datetime.fromisoformat(clean)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        # Try standard date YYYY-MM-DD
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(clean, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


class AuditExporter:
    """Coordinates secure audit trail query, data scrubbing, and JSON/CSV export."""

    def __init__(
        self,
        export_dir: Optional[Union[str, Path]] = None,
        engine: Optional[Engine] = None,
    ):
        if export_dir is not None:
            self._export_dir = Path(export_dir).resolve()
        else:
            self._export_dir = get_export_dir().resolve()

        # Create export directory safely
        self._export_dir.mkdir(parents=True, exist_ok=True)
        self._engine = engine
        self._export_records: Dict[str, Dict[str, Any]] = {}

    @property
    def export_dir(self) -> Path:
        """Return canonical approved export directory."""
        return self._export_dir

    @property
    def engine(self) -> Engine:
        """Return synchronous database engine."""
        if self._engine is None:
            self._engine = get_sync_engine()
        return self._engine

    def validate_destination_path(self, filename: str) -> Path:
        """Enforce strict filename safety and export directory containment.

        Prevents path traversal attacks (../, absolute paths, backslashes).
        """
        if not filename or not filename.strip():
            raise ValueError("Export filename cannot be empty.")

        clean_name = filename.strip()
        if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
            raise ValueError(
                f"Export filename contains forbidden path separators or traversal sequences: '{filename}'"
            )

        target = (self._export_dir / clean_name).resolve()

        # Strict containment verification
        try:
            if not target.is_relative_to(self._export_dir):
                raise ValueError("Path traversal detected: destination file is outside approved export directory.")
        except (ValueError, TypeError) as err:
            raise ValueError(f"Path traversal detected: {err}") from err

        return target

    def query_audit_records(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Query execution and step records from persistent database with optional filters.

        All returned records are thoroughly scrubbed of tokens, passwords, and sensitive keys.
        """
        filters = filters or {}
        exec_id_filter = filters.get("execution_id")
        conv_id_filter = filters.get("conversation_id")
        status_filter = filters.get("status") or filters.get("status_filter")
        start_date = parse_filter_datetime(filters.get("start_date") or filters.get("start_time"))
        end_date = parse_filter_datetime(filters.get("end_date") or filters.get("end_time"))

        with Session(self.engine) as session:
            stmt = select(ExecutionRecord)

            if exec_id_filter:
                stmt = stmt.where(ExecutionRecord.execution_id == str(exec_id_filter).strip())
            if conv_id_filter:
                stmt = stmt.where(ExecutionRecord.conversation_id == str(conv_id_filter).strip())
            if status_filter:
                stmt = stmt.where(ExecutionRecord.execution_status == str(status_filter).strip())
            if start_date:
                stmt = stmt.where(ExecutionRecord.created_at >= start_date)
            if end_date:
                stmt = stmt.where(ExecutionRecord.created_at <= end_date)

            stmt = stmt.order_by(ExecutionRecord.created_at.desc())
            executions = list(session.exec(stmt).all())

            results: List[Dict[str, Any]] = []
            for exec_rec in executions:
                # Query associated steps
                step_stmt = (
                    select(ExecutionStepRecord)
                    .where(ExecutionStepRecord.execution_id == exec_rec.execution_id)
                    .order_by(ExecutionStepRecord.step_index.asc())
                )
                steps = list(session.exec(step_stmt).all())

                rec_dict = {
                    "execution_id": exec_rec.execution_id,
                    "conversation_id": exec_rec.conversation_id,
                    "user_input": exec_rec.user_input,
                    "intent": exec_rec.intent,
                    "execution_status": exec_rec.execution_status,
                    "plan": exec_rec.get_plan(),
                    "response": exec_rec.response,
                    "error": exec_rec.error,
                    "created_at": exec_rec.created_at.isoformat() if exec_rec.created_at else None,
                    "completed_at": exec_rec.completed_at.isoformat() if exec_rec.completed_at else None,
                    "steps": [s.to_dict() for s in steps],
                }

                # Thoroughly scrub all sensitive credentials and tokens
                scrubbed_rec = scrub_sensitive_data(rec_dict)
                results.append(scrubbed_rec)

            return results

    def export_json(
        self,
        filename: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Export scrubbed audit records to a structured JSON file in the approved directory.

        Never opens or executes the exported file.
        """
        export_id = f"exp_{uuid.uuid4().hex[:12]}"
        now_ts = datetime.now(timezone.utc).isoformat()

        if not filename:
            filename = f"audit_export_{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
        elif not filename.lower().endswith(".json"):
            filename = f"{filename}.json"

        target_path = self.validate_destination_path(filename)

        if target_path.exists() and not confirm_overwrite:
            raise FileExistsError(
                f"Target export file already exists: '{target_path.name}'. "
                "Specify confirm_overwrite=True to overwrite existing export."
            )

        records = self.query_audit_records(filters)

        payload = {
            "export_id": export_id,
            "exported_at": now_ts,
            "format": "json",
            "filter_criteria": filters or {},
            "record_count": len(records),
            "records": records,
        }

        # Write safely without launching or executing
        target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info(f"Audit export generated ({len(records)} records) -> {target_path}")

        meta = {
            "export_id": export_id,
            "filename": target_path.name,
            "file_path": str(target_path),
            "format": "json",
            "record_count": len(records),
            "exported_at": now_ts,
            "status": "completed",
        }
        self._export_records[export_id] = meta
        return meta

    def export_csv(
        self,
        filename: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Export scrubbed audit records to a CSV file in the approved directory.

        Never opens or executes the exported file.
        """
        export_id = f"exp_{uuid.uuid4().hex[:12]}"
        now_ts = datetime.now(timezone.utc).isoformat()

        if not filename:
            filename = f"audit_export_{int(time.time())}_{uuid.uuid4().hex[:6]}.csv"
        elif not filename.lower().endswith(".csv"):
            filename = f"{filename}.csv"

        target_path = self.validate_destination_path(filename)

        if target_path.exists() and not confirm_overwrite:
            raise FileExistsError(
                f"Target export file already exists: '{target_path.name}'. "
                "Specify confirm_overwrite=True to overwrite existing export."
            )

        records = self.query_audit_records(filters)

        fieldnames = [
            "execution_id",
            "conversation_id",
            "user_input",
            "intent",
            "execution_status",
            "step_index",
            "action",
            "step_status",
            "result_summary",
            "error",
            "created_at",
            "completed_at",
        ]

        with target_path.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for rec in records:
                steps = rec.get("steps") or []
                if not steps:
                    writer.writerow({
                        "execution_id": rec.get("execution_id"),
                        "conversation_id": rec.get("conversation_id"),
                        "user_input": rec.get("user_input"),
                        "intent": rec.get("intent"),
                        "execution_status": rec.get("execution_status"),
                        "step_index": "",
                        "action": "",
                        "step_status": "",
                        "result_summary": rec.get("response", ""),
                        "error": rec.get("error", ""),
                        "created_at": rec.get("created_at"),
                        "completed_at": rec.get("completed_at"),
                    })
                else:
                    for s in steps:
                        writer.writerow({
                            "execution_id": rec.get("execution_id"),
                            "conversation_id": rec.get("conversation_id"),
                            "user_input": rec.get("user_input"),
                            "intent": rec.get("intent"),
                            "execution_status": rec.get("execution_status"),
                            "step_index": s.get("step_index"),
                            "action": s.get("action"),
                            "step_status": s.get("execution_status"),
                            "result_summary": s.get("result_summary", ""),
                            "error": s.get("error", ""),
                            "created_at": s.get("started_at") or rec.get("created_at"),
                            "completed_at": s.get("completed_at") or rec.get("completed_at"),
                        })

        logger.info(f"Audit CSV export generated ({len(records)} records) -> {target_path}")

        meta = {
            "export_id": export_id,
            "filename": target_path.name,
            "file_path": str(target_path),
            "format": "csv",
            "record_count": len(records),
            "exported_at": now_ts,
            "status": "completed",
        }
        self._export_records[export_id] = meta
        return meta

    def get_export_status(self, export_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached metadata for an export ID."""
        return self._export_records.get(export_id)

    def list_exports(self) -> List[Dict[str, Any]]:
        """List all generated audit exports, sorted with newest first.

        Scans the approved export directory to discover any existing files
        and never executes or opens them.
        """
        # Populate any files in export_dir not already in memory
        try:
            if self._export_dir.exists():
                for item in self._export_dir.iterdir():
                    if item.is_file() and item.suffix.lower() in {".json", ".csv"}:
                        # Check if already present by filename
                        already_known = any(
                            r.get("filename") == item.name for r in self._export_records.values()
                        )
                        if not already_known:
                            pseudo_id = f"exp_{item.stem[:12]}"
                            stat = item.stat()
                            now_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                            self._export_records[pseudo_id] = {
                                "export_id": pseudo_id,
                                "filename": item.name,
                                "file_path": str(item.resolve()),
                                "format": item.suffix.lower().lstrip("."),
                                "record_count": 0,
                                "exported_at": now_iso,
                                "status": "completed",
                            }
        except Exception as scan_err:
            logger.warning(f"Error scanning export directory: {scan_err}")

        records = list(self._export_records.values())
        records.sort(key=lambda r: str(r.get("exported_at", "")), reverse=True)
        return records


# ==============================================================================
# Shared Singleton Management
# ==============================================================================

_SHARED_AUDIT_EXPORTER: Optional[AuditExporter] = None


def get_shared_audit_exporter() -> AuditExporter:
    """Retrieve or initialize shared AuditExporter singleton."""
    global _SHARED_AUDIT_EXPORTER
    if _SHARED_AUDIT_EXPORTER is None:
        _SHARED_AUDIT_EXPORTER = AuditExporter()
    return _SHARED_AUDIT_EXPORTER


def set_shared_audit_exporter(exporter: AuditExporter) -> None:
    """Explicitly inject an AuditExporter singleton (e.g. for testing)."""
    global _SHARED_AUDIT_EXPORTER
    _SHARED_AUDIT_EXPORTER = exporter

