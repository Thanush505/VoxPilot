"""
VoxPilot - Custom Automation Builder, Scheduler & Execution Engine (Phase 6A)

Provides:
- Natural-language-to-automation preview & validation (never auto-executes on creation).
- Strict validation against ToolRegistry & Pydantic schemas (rejecting shell/arbitrary execution).
- Profile-isolated CRUD persistence in SQLite (`automations` table).
- Sequential execution via ToolExecutor, honoring ConfirmationManager and EmergencyStopManager.
- Lightweight local-timezone in-app scheduler (`daily` / `weekly`) with duplicate-slot
  prevention and safe missed-run skipping on restart.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import (
    ToolArgumentValidationError,
    ToolRegistry,
    UnknownToolError,
    get_shared_tool_registry,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.automation.notifications import sanitize_notification_text
from backend.database.connection import get_sync_engine
from backend.database.models import (
    AutomationExecutionRecord,
    AutomationExecutionStepRecord,
    AutomationRecord,
)
from backend.profiles.service import get_shared_profile_service

logger = logging.getLogger(__name__)

VALID_TRIGGER_TYPES = ("manual", "scheduled", "user_requested")
VALID_FREQUENCIES = ("daily", "weekly")
FORBIDDEN_ACTIONS = frozenset({
    "execute_shell",
    "run_command",
    "shell",
    "powershell",
    "cmd",
    "bash",
    "eval",
    "exec",
})
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{12,}|AIza[A-Za-z0-9_\-]{12,}|bearer\s+[A-Za-z0-9._\-]{10,}|password\s*[:=]\s*\S+)"
)
_TIME_24H_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TIME_NL_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\?)*")

_WEEKDAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


class AutomationError(Exception):
    """Base exception for custom automation operations."""


class AutomationValidationError(AutomationError):
    """Raised when an automation definition, trigger, or step is invalid."""


class AutomationNotFoundError(AutomationError):
    """Raised when an automation or execution does not exist."""


class AutomationAccessError(AutomationError):
    """Raised when a profile attempts to access another profile's automation."""


def _local_timezone_name() -> str:
    try:
        tz = datetime.now().astimezone().tzname()
        return tz or "Local"
    except Exception:
        return "Local"


def validate_trigger_config(trigger_type: str, trigger_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise AutomationValidationError(
            f"Unsupported trigger_type '{trigger_type}'. Supported: {', '.join(VALID_TRIGGER_TYPES)}."
        )
    cfg = dict(trigger_config or {})
    if trigger_type != "scheduled":
        return {}

    freq = str(cfg.get("frequency") or "daily").lower()
    if freq not in VALID_FREQUENCIES:
        raise AutomationValidationError(
            f"Unsupported schedule frequency '{freq}'. Supported: {', '.join(VALID_FREQUENCIES)}."
        )
    time_of_day = str(cfg.get("time_of_day") or "09:00").strip()
    if not _TIME_24H_RE.match(time_of_day):
        raise AutomationValidationError(
            f"Invalid time_of_day '{time_of_day}'. Expected 24-hour HH:MM format (e.g. '09:00')."
        )
    weekdays = cfg.get("weekdays")
    if freq == "weekly":
        if not isinstance(weekdays, list) or not weekdays:
            weekdays = [0]
        cleaned_days: List[int] = []
        for d in weekdays:
            if not isinstance(d, int) or d < 0 or d > 6:
                raise AutomationValidationError("Weekdays must be integers 0 (Monday) through 6 (Sunday).")
            if d not in cleaned_days:
                cleaned_days.append(d)
    else:
        cleaned_days = []

    return {
        "frequency": freq,
        "time_of_day": time_of_day,
        "weekdays": cleaned_days,
        "timezone": str(cfg.get("timezone") or _local_timezone_name()),
        "paused": bool(cfg.get("paused", False)),
        "last_scheduled_slot": cfg.get("last_scheduled_slot"),
    }


def _assert_no_secrets(value: Any) -> None:
    if isinstance(value, str):
        if _SECRET_PATTERN.search(value):
            raise AutomationValidationError("Automation definitions must not contain secrets or API tokens.")
    elif isinstance(value, dict):
        for v in value.values():
            _assert_no_secrets(v)
    elif isinstance(value, list):
        for v in value:
            _assert_no_secrets(v)


def _redact_summary(text: Optional[str], max_length: int = 180) -> Optional[str]:
    if not text:
        return None
    return sanitize_notification_text(str(text), max_length=max_length)


def _safe_input_summary(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return "{}"
    safe_dict: Dict[str, Any] = {}
    sensitive_keys = {"password", "passwd", "token", "secret", "api_key", "confirmation_token", "content", "body"}
    for k, v in params.items():
        lk = str(k).lower()
        if lk in sensitive_keys:
            safe_dict[k] = "[redacted]"
        elif isinstance(v, str):
            safe_dict[k] = sanitize_notification_text(v, max_length=80)
        else:
            safe_dict[k] = v
    return json.dumps(safe_dict)[:240]


def validate_automation_steps(
    steps: List[Dict[str, Any]],
    registry: Optional[ToolRegistry] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(steps, list) or len(steps) == 0:
        raise AutomationValidationError("Automation must include at least one action step.")
    if len(steps) > 25:
        raise AutomationValidationError("Automation cannot exceed 25 steps.")

    reg = registry or get_shared_tool_registry()
    validated: List[Dict[str, Any]] = []

    for idx, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise AutomationValidationError(f"Step {idx + 1} must be an object.")
        raw_action = str(raw_step.get("action") or "").strip()
        if not raw_action:
            raise AutomationValidationError(f"Step {idx + 1} is missing an 'action'.")
        if raw_action.lower() in FORBIDDEN_ACTIONS:
            raise AutomationValidationError(
                f"Action '{raw_action}' is blocked by VoxPilot safety policy."
            )
        raw_params = raw_step.get("parameters") or {}
        if not isinstance(raw_params, dict):
            raise AutomationValidationError(f"Step {idx + 1} parameters must be a JSON object.")

        _assert_no_secrets(raw_params)

        try:
            canonical_action = reg.resolve_name(raw_action)
            normalized_params = reg.normalize_parameters(canonical_action, raw_params)
            tool = reg.get_tool(canonical_action)
            if tool.input_schema is not None:
                model_obj = tool.input_schema(**normalized_params)
                clean_params = model_obj.model_dump()
            else:
                clean_params = dict(normalized_params)
        except (UnknownToolError, ToolArgumentValidationError) as err:
            raise AutomationValidationError(f"Step {idx + 1} ('{raw_action}'): {err}") from err
        except Exception as err:
            raise AutomationValidationError(f"Step {idx + 1} ('{raw_action}') invalid arguments: {err}") from err

        validated.append({
            "step_id": str(raw_step.get("step_id") or f"step_{idx + 1}"),
            "action": canonical_action,
            "parameters": clean_params,
            "description": str(raw_step.get("description") or tool.description or canonical_action),
        })

    return validated


def preview_from_natural_language(
    prompt: str,
    registry: Optional[ToolRegistry] = None,
) -> Dict[str, Any]:
    """Convert a natural-language automation request into a validated preview (never saves/runs)."""
    text = (prompt or "").strip()
    if not text:
        raise AutomationValidationError("Please describe the automation you want to create.")

    reg = registry or get_shared_tool_registry()
    lower = text.lower()
    steps: List[Dict[str, Any]] = []
    missing_fields: List[str] = []
    warnings: List[str] = []

    # 1. Detect schedule / trigger
    trigger_type = "manual"
    trigger_config: Dict[str, Any] = {}
    if any(kw in lower for kw in ("every day", "daily", "every morning", "every evening", "every night", "every week", "weekly")) or any(
        f"every {d}" in lower for d in _WEEKDAY_MAP
    ):
        trigger_type = "scheduled"
        freq = "daily"
        weekdays: List[int] = []
        for name, day_idx in _WEEKDAY_MAP.items():
            if re.search(rf"\b{name}\b", lower):
                freq = "weekly"
                if day_idx not in weekdays:
                    weekdays.append(day_idx)
        if "weekly" in lower and not weekdays:
            freq = "weekly"
            weekdays = [0]

        time_of_day = "09:00"
        if "morning" in lower:
            time_of_day = "09:00"
        elif "evening" in lower:
            time_of_day = "18:00"
        elif "night" in lower:
            time_of_day = "21:00"

        for m in _TIME_NL_RE.finditer(lower):
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = (m.group(3) or "").lower()
            if ampm or m.group(2) is not None:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    time_of_day = f"{hour:02d}:{minute:02d}"
                    break

        trigger_config = validate_trigger_config("scheduled", {
            "frequency": freq,
            "time_of_day": time_of_day,
            "weekdays": weekdays,
        })

    # 2. Reject explicit shell/arbitrary execution requests
    if any(bad in lower for bad in ("powershell", "command prompt", "bash script", "shell command", "run cmd")):
        raise AutomationValidationError(
            "Arbitrary shell or command-line execution is not permitted in VoxPilot automations."
        )

    # 3. Extract structured steps in mention order
    # Application launches
    app_patterns = [
        ("Google Chrome", ("chrome", "google chrome")),
        ("Visual Studio Code", ("vs code", "vscode", "visual studio code")),
        ("Notepad", ("notepad",)),
        ("Calculator", ("calculator", "calc")),
        ("File Explorer", ("file explorer", "explorer")),
    ]
    found_apps: List[tuple[int, str]] = []
    for canonical_app, aliases in app_patterns:
        for alias in aliases:
            pos = lower.find(alias)
            if pos != -1:
                found_apps.append((pos, canonical_app))
                break
    found_apps.sort(key=lambda item: item[0])
    for _, app_name in found_apps:
        steps.append({
            "action": "launch_application",
            "parameters": {"application_name": app_name},
            "description": f"Launch {app_name}",
        })

    # Development environment shorthand
    if "development environment" in lower or "dev environment" in lower:
        if not any(s["parameters"].get("application_name") == "Visual Studio Code" for s in steps):
            steps.append({
                "action": "launch_application",
                "parameters": {"application_name": "Visual Studio Code"},
                "description": "Launch Visual Studio Code",
            })
        if not any(s["parameters"].get("application_name") == "Google Chrome" for s in steps):
            steps.append({
                "action": "launch_application",
                "parameters": {"application_name": "Google Chrome"},
                "description": "Launch Google Chrome",
            })

    # Folder inspection / listing
    path_match = _WIN_PATH_RE.search(text)
    if any(kw in lower for kw in ("check a folder", "check folder", "new files", "list files", "project folder")):
        if path_match:
            steps.append({
                "action": "list_directory",
                "parameters": {"directory_path": path_match.group(0)},
                "description": f"List files in {path_match.group(0)}",
            })
        elif "voxpilot" in lower:
            steps.append({
                "action": "list_directory",
                "parameters": {"directory_path": "."},
                "description": "Inspect VoxPilot project directory",
            })
        else:
            missing_fields.append("directory_path")
            warnings.append("Please specify the folder path you want this automation to inspect.")

    # Writing notes
    if "write" in lower and ("note" in lower or "notepad" in lower):
        note_match = re.search(r"(?:write|type)\s+(?:my\s+)?(.+?)(?:\s+in\s+notepad|$)", text, re.IGNORECASE)
        note_text = note_match.group(1).strip() if note_match else "Daily notes"
        steps.append({
            "action": "type_text",
            "parameters": {"application_name": "Notepad", "text": note_text},
            "description": "Type daily notes into Notepad",
        })

    # URL navigation
    url_match = _URL_RE.search(text)
    if url_match:
        steps.append({
            "action": "start_browser",
            "parameters": {"headless": False},
            "description": "Start VoxPilot browser session",
        })
        steps.append({
            "action": "navigate_url",
            "parameters": {"url": url_match.group(0)},
            "description": f"Open {url_match.group(0)}",
        })

    # Fallback safe status check for reminders
    if not steps and ("remind" in lower or "check" in lower):
        steps.append({
            "action": "get_system_info",
            "parameters": {},
            "description": "Run readiness check & notify",
        })
        warnings.append("Review or customize the generated step before saving.")

    if not steps:
        missing_fields.append("steps")
        warnings.append(
            "Could not automatically map all actions. Add or edit steps manually before saving."
        )
        validated_steps: List[Dict[str, Any]] = []
    else:
        validated_steps = validate_automation_steps(steps, registry=reg)

    name = text[:56].rstrip(" .") + ("..." if len(text) > 56 else "")
    return {
        "name": name,
        "description": text,
        "trigger_type": trigger_type,
        "trigger_config": trigger_config,
        "steps": validated_steps,
        "clarification_needed": len(missing_fields) > 0,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "requires_approval": True,
    }


class CustomAutomationService:
    """Manages profile-isolated custom automations, scheduling, and execution."""

    def __init__(
        self,
        engine=None,
        registry: Optional[ToolRegistry] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._tool_executor = tool_executor
        self._lock = threading.RLock()
        self._executions: Dict[str, Dict[str, Any]] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    def _get_engine(self):
        return self._engine if self._engine is not None else get_sync_engine()

    def _get_registry(self) -> ToolRegistry:
        return self._registry if self._registry is not None else get_shared_tool_registry()

    def _get_executor(self) -> ToolExecutor:
        return self._tool_executor if self._tool_executor is not None else ToolExecutor(registry=self._get_registry())

    @staticmethod
    def _resolve_profile_id(profile_id: Optional[str] = None) -> str:
        if profile_id and profile_id.strip():
            return profile_id.strip()
        try:
            return get_shared_profile_service().get_active_profile_id() or "default"
        except Exception:
            return "default"

    def preview_automation(self, prompt: str) -> Dict[str, Any]:
        return preview_from_natural_language(prompt, registry=self._get_registry())

    def create_automation(
        self,
        *,
        name: str,
        description: str = "",
        trigger_type: str = "manual",
        trigger_config: Optional[Dict[str, Any]] = None,
        steps: List[Dict[str, Any]],
        enabled: bool = True,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise AutomationValidationError("Automation name is required.")
        _assert_no_secrets(clean_name)
        _assert_no_secrets(description)

        clean_trigger_cfg = validate_trigger_config(trigger_type, trigger_config)
        clean_steps = validate_automation_steps(steps, registry=self._get_registry())
        pid = self._resolve_profile_id(profile_id)

        record = AutomationRecord(
            profile_id=pid,
            name=clean_name[:120],
            description=(description or "")[:500],
            trigger_type=trigger_type,
            trigger_config_json=json.dumps(clean_trigger_cfg),
            steps_json=json.dumps(clean_steps),
            enabled=bool(enabled),
        )
        with Session(self._get_engine()) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record.to_dict()

    def list_automations(self, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            stmt = (
                select(AutomationRecord)
                .where(AutomationRecord.profile_id == pid)
                .order_by(AutomationRecord.created_at.desc())
            )
            records = session.exec(stmt).all()
            return [r.to_dict() for r in records]

    def _get_owned_record(self, session: Session, automation_id: str, profile_id: Optional[str] = None) -> AutomationRecord:
        pid = self._resolve_profile_id(profile_id)
        record = session.get(AutomationRecord, automation_id)
        if record is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found.")
        if record.profile_id != pid:
            raise AutomationAccessError(
                f"Automation '{automation_id}' does not belong to profile '{pid}'."
            )
        return record

    def get_automation(self, automation_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        with Session(self._get_engine()) as session:
            return self._get_owned_record(session, automation_id, profile_id).to_dict()

    def update_automation(
        self,
        automation_id: str,
        updates: Dict[str, Any],
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with Session(self._get_engine()) as session:
            record = self._get_owned_record(session, automation_id, profile_id)
            if "name" in updates and updates["name"] is not None:
                clean_name = str(updates["name"]).strip()
                if not clean_name:
                    raise AutomationValidationError("Automation name cannot be empty.")
                _assert_no_secrets(clean_name)
                record.name = clean_name[:120]
            if "description" in updates and updates["description"] is not None:
                _assert_no_secrets(str(updates["description"]))
                record.description = str(updates["description"])[:500]
            if "enabled" in updates and updates["enabled"] is not None:
                record.enabled = bool(updates["enabled"])
            if "trigger_type" in updates or "trigger_config" in updates:
                next_type = str(updates.get("trigger_type") or record.trigger_type)
                existing_cfg = json.loads(record.trigger_config_json or "{}")
                next_cfg = updates.get("trigger_config", existing_cfg)
                record.trigger_type = next_type
                record.trigger_config_json = json.dumps(validate_trigger_config(next_type, next_cfg))
            if "steps" in updates and updates["steps"] is not None:
                clean_steps = validate_automation_steps(updates["steps"], registry=self._get_registry())
                record.steps_json = json.dumps(clean_steps)
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record.to_dict()

    def toggle_automation(self, automation_id: str, enabled: bool, profile_id: Optional[str] = None) -> Dict[str, Any]:
        return self.update_automation(automation_id, {"enabled": enabled}, profile_id=profile_id)

    def update_schedule(
        self,
        automation_id: str,
        trigger_config: Dict[str, Any],
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.update_automation(
            automation_id,
            {"trigger_type": "scheduled", "trigger_config": trigger_config},
            profile_id=profile_id,
        )

    def delete_automation(self, automation_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        with Session(self._get_engine()) as session:
            record = self._get_owned_record(session, automation_id, profile_id)
            session.delete(record)
            session.commit()
            return {"deleted": True, "automation_id": automation_id}

    def execute_automation(
        self,
        automation_id: str,
        *,
        profile_id: Optional[str] = None,
        confirmation_token: Optional[str] = None,
        trigger_source: str = "manual",
    ) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        auto_dict = self.get_automation(automation_id, profile_id=pid)

        if get_shared_emergency_stop_manager().is_emergency_stop_active():
            raise AutomationValidationError(
                "Cannot execute automation while Global Emergency Stop is active."
            )

        steps = validate_automation_steps(auto_dict["steps"], registry=self._get_registry())
        execution_id = f"autoexec_{uuid.uuid4().hex[:12]}"
        cancel_event = threading.Event()

        started_dt = datetime.now(timezone.utc)
        started_mono = time.monotonic()

        step_states = [
            {
                "step_index": i,
                "step_id": s["step_id"],
                "action": s["action"],
                "description": s["description"],
                "input_summary": _safe_input_summary(s.get("parameters")),
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "message": None,
                "output_summary": None,
                "error": None,
                "error_summary": None,
            }
            for i, s in enumerate(steps)
        ]

        exec_state: Dict[str, Any] = {
            "execution_id": execution_id,
            "automation_id": automation_id,
            "automation_name": auto_dict["name"],
            "profile_id": pid,
            "trigger_type": trigger_source,
            "trigger_source": trigger_source,
            "status": "running",
            "current_step": 0,
            "total_steps": len(steps),
            "completed_steps": 0,
            "failed_step_index": None,
            "cancelled": False,
            "duration_ms": None,
            "steps": step_states,
            "pending_confirmation": None,
            "error": None,
            "error_summary": None,
            "started_at": started_dt.isoformat(),
            "finished_at": None,
            "completed_at": None,
        }
        with self._lock:
            self._executions[execution_id] = exec_state
            self._cancel_flags[execution_id] = cancel_event

        # Persist initial execution & ordered step records
        with Session(self._get_engine()) as session:
            session.add(
                AutomationExecutionRecord(
                    execution_id=execution_id,
                    automation_id=automation_id,
                    profile_id=pid,
                    automation_name=auto_dict["name"],
                    trigger_type=trigger_source,
                    status="running",
                    total_steps=len(steps),
                    completed_steps=0,
                    started_at=started_dt,
                )
            )
            for st in step_states:
                session.add(
                    AutomationExecutionStepRecord(
                        execution_id=execution_id,
                        step_index=st["step_index"],
                        step_id=st["step_id"],
                        action=st["action"],
                        description=st["description"],
                        input_summary=st["input_summary"],
                        status="pending",
                    )
                )
            session.commit()

        executor = self._get_executor()
        final_status = "completed"
        final_error: Optional[str] = None
        completed_count = 0
        failed_idx: Optional[int] = None

        try:
            for idx, step in enumerate(steps):
                if cancel_event.is_set() or get_shared_emergency_stop_manager().is_emergency_stop_active():
                    final_status = "cancelled"
                    final_error = "Execution cancelled."
                    step_states[idx]["status"] = "cancelled"
                    self._persist_step_update(execution_id, idx, status="cancelled", error_summary=final_error)
                    break

                step_start = datetime.now(timezone.utc)
                with self._lock:
                    exec_state["current_step"] = idx
                    step_states[idx]["status"] = "running"
                    step_states[idx]["started_at"] = step_start.isoformat()
                self._persist_step_update(execution_id, idx, status="running", started_at=step_start)

                result = executor.execute_step(
                    {
                        "action": step["action"],
                        "parameters": step["parameters"],
                        "execution_id": execution_id,
                    },
                    step_index=idx,
                    override_confirmation_token=confirmation_token,
                )
                step_end = datetime.now(timezone.utc)

                if result.get("status") == "confirmation_required":
                    final_status = "confirmation_required"
                    safe_msg = _redact_summary(result.get("message"))
                    pending_data = result.get("data") or {
                        "message": safe_msg,
                        "action": step["action"],
                    }
                    with self._lock:
                        step_states[idx]["status"] = "confirmation_required"
                        step_states[idx]["completed_at"] = step_end.isoformat()
                        step_states[idx]["message"] = safe_msg
                        step_states[idx]["output_summary"] = safe_msg
                        exec_state["pending_confirmation"] = pending_data
                    self._persist_step_update(
                        execution_id,
                        idx,
                        status="confirmation_required",
                        completed_at=step_end,
                        output_summary=safe_msg,
                    )
                    break

                if not result.get("success", False):
                    final_status = "cancelled" if result.get("status") == "cancelled" else "failed"
                    failed_idx = idx if final_status == "failed" else None
                    final_error = _redact_summary(
                        result.get("error") or result.get("message") or "Step failed."
                    )
                    safe_msg = _redact_summary(result.get("message"))
                    with self._lock:
                        step_states[idx]["status"] = final_status
                        step_states[idx]["completed_at"] = step_end.isoformat()
                        step_states[idx]["error"] = final_error
                        step_states[idx]["error_summary"] = final_error
                        step_states[idx]["message"] = safe_msg
                        step_states[idx]["output_summary"] = safe_msg
                    self._persist_step_update(
                        execution_id,
                        idx,
                        status=final_status,
                        completed_at=step_end,
                        output_summary=safe_msg,
                        error_summary=final_error,
                    )
                    break

                completed_count += 1
                safe_msg = _redact_summary(result.get("message"))
                with self._lock:
                    step_states[idx]["status"] = "completed"
                    step_states[idx]["completed_at"] = step_end.isoformat()
                    step_states[idx]["message"] = safe_msg
                    step_states[idx]["output_summary"] = safe_msg
                self._persist_step_update(
                    execution_id,
                    idx,
                    status="completed",
                    completed_at=step_end,
                    output_summary=safe_msg,
                )
        except Exception as err:
            final_status = "failed"
            failed_idx = exec_state.get("current_step", 0)
            final_error = _redact_summary(f"Unexpected execution error: {err}")
            logger.error("Automation '%s' failed unexpectedly: %s", automation_id, err, exc_info=True)
            if 0 <= failed_idx < len(step_states):
                step_states[failed_idx]["status"] = "failed"
                step_states[failed_idx]["error"] = final_error
                step_states[failed_idx]["error_summary"] = final_error
                self._persist_step_update(
                    execution_id,
                    failed_idx,
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error_summary=final_error,
                )

        finished_dt = datetime.now(timezone.utc)
        duration_ms = max(0, int((time.monotonic() - started_mono) * 1000))
        is_cancelled = final_status == "cancelled"

        with self._lock:
            exec_state["status"] = final_status
            exec_state["completed_steps"] = completed_count
            exec_state["failed_step_index"] = failed_idx
            exec_state["cancelled"] = is_cancelled
            exec_state["duration_ms"] = duration_ms
            exec_state["error"] = final_error
            exec_state["error_summary"] = final_error
            exec_state["finished_at"] = finished_dt.isoformat()
            exec_state["completed_at"] = finished_dt.isoformat()

        with Session(self._get_engine()) as session:
            rec = session.get(AutomationRecord, automation_id)
            if rec is not None:
                rec.last_execution_id = execution_id
                rec.last_execution_status = final_status
                rec.last_run_at = finished_dt
                session.add(rec)

            hist_rec = session.get(AutomationExecutionRecord, execution_id)
            if hist_rec is not None:
                hist_rec.status = final_status
                hist_rec.completed_steps = completed_count
                hist_rec.failed_step_index = failed_idx
                hist_rec.cancelled = is_cancelled
                hist_rec.error_summary = final_error
                hist_rec.duration_ms = duration_ms
                hist_rec.completed_at = finished_dt
                if exec_state.get("pending_confirmation"):
                    hist_rec.pending_confirmation_json = json.dumps(exec_state["pending_confirmation"])
                session.add(hist_rec)
            session.commit()

        self._notify_outcome(
            auto_dict["name"],
            execution_id,
            final_status,
            final_error,
            pid,
            duration_ms=duration_ms,
        )
        return dict(exec_state)

    def _persist_step_update(
        self,
        execution_id: str,
        step_index: int,
        *,
        status: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        output_summary: Optional[str] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        with Session(self._get_engine()) as session:
            stmt = select(AutomationExecutionStepRecord).where(
                AutomationExecutionStepRecord.execution_id == execution_id,
                AutomationExecutionStepRecord.step_index == step_index,
            )
            step_rec = session.exec(stmt).first()
            if step_rec is None:
                return
            if status is not None:
                step_rec.status = status
            if started_at is not None:
                step_rec.started_at = started_at
            if completed_at is not None:
                step_rec.completed_at = completed_at
            if output_summary is not None:
                step_rec.output_summary = output_summary
            if error_summary is not None:
                step_rec.error_summary = error_summary
            session.add(step_rec)
            session.commit()

    @staticmethod
    def _notify_outcome(
        name: str,
        execution_id: str,
        status_val: str,
        error: Optional[str],
        profile_id: str,
        duration_ms: int = 0,
    ) -> None:
        try:
            from backend.automation.notifications import get_shared_notification_service

            if status_val == "completed":
                category = "long_running_task_completed" if duration_ms >= 8000 else "automation_completed"
                get_shared_notification_service().notify(
                    category=category,
                    title="Automation Completed",
                    message=f"Automation '{name}' finished.",
                    event_key=f"auto:{execution_id}:completed",
                    profile_id=profile_id,
                )
            elif status_val == "failed":
                get_shared_notification_service().notify(
                    category="automation_failed",
                    title="Automation Failed",
                    message=error or f"Automation '{name}' failed.",
                    event_key=f"auto:{execution_id}:failed",
                    profile_id=profile_id,
                )
            elif status_val == "confirmation_required":
                get_shared_notification_service().notify(
                    category="confirmation_required",
                    title="Automation Confirmation Required",
                    message=f"Automation '{name}' requires your confirmation in VoxPilot.",
                    event_key=f"auto:{execution_id}:confirm",
                    profile_id=profile_id,
                    sensitive=True,
                )
        except Exception:
            pass

    def get_execution_status(self, execution_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        with self._lock:
            exec_state = self._executions.get(execution_id)
            if exec_state is not None:
                if exec_state["profile_id"] != pid:
                    raise AutomationAccessError("Execution belongs to another profile.")
                return dict(exec_state)

        with Session(self._get_engine()) as session:
            hist = session.get(AutomationExecutionRecord, execution_id)
            if hist is None:
                raise AutomationNotFoundError(f"Execution '{execution_id}' not found.")
            if hist.profile_id != pid:
                raise AutomationAccessError("Execution belongs to another profile.")
            steps = session.exec(
                select(AutomationExecutionStepRecord)
                .where(AutomationExecutionStepRecord.execution_id == execution_id)
                .order_by(AutomationExecutionStepRecord.step_index.asc())
            ).all()
            payload = hist.to_dict()
            payload["steps"] = [s.to_dict() for s in steps]
            return payload

    def list_execution_history(
        self,
        *,
        profile_id: Optional[str] = None,
        automation_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        trigger_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))

        with Session(self._get_engine()) as session:
            stmt = select(AutomationExecutionRecord).where(
                AutomationExecutionRecord.profile_id == pid
            )
            if automation_id:
                stmt = stmt.where(AutomationExecutionRecord.automation_id == automation_id)
            if status_filter:
                stmt = stmt.where(AutomationExecutionRecord.status == status_filter)
            if trigger_type:
                stmt = stmt.where(AutomationExecutionRecord.trigger_type == trigger_type)
            if date_from:
                try:
                    dt_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
                    stmt = stmt.where(AutomationExecutionRecord.started_at >= dt_from)
                except ValueError:
                    pass
            if date_to:
                try:
                    dt_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
                    stmt = stmt.where(AutomationExecutionRecord.started_at <= dt_to)
                except ValueError:
                    pass

            all_matching = session.exec(
                stmt.order_by(AutomationExecutionRecord.started_at.desc())
            ).all()
            total = len(all_matching)
            page = all_matching[safe_offset : safe_offset + safe_limit]
            return {
                "items": [r.to_dict() for r in page],
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

    def cancel_execution(self, execution_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        with self._lock:
            exec_state = self._executions.get(execution_id)
            if exec_state is not None:
                if exec_state["profile_id"] != pid:
                    raise AutomationAccessError("Execution belongs to another profile.")
                ev = self._cancel_flags.get(execution_id)
                if ev is not None:
                    ev.set()
                if exec_state["status"] == "running":
                    now_dt = datetime.now(timezone.utc)
                    exec_state["status"] = "cancelled"
                    exec_state["cancelled"] = True
                    exec_state["finished_at"] = now_dt.isoformat()
                    exec_state["completed_at"] = now_dt.isoformat()

        with Session(self._get_engine()) as session:
            hist = session.get(AutomationExecutionRecord, execution_id)
            if hist is None:
                if exec_state is None:
                    raise AutomationNotFoundError(f"Execution '{execution_id}' not found.")
                return dict(exec_state)
            if hist.profile_id != pid:
                raise AutomationAccessError("Execution belongs to another profile.")
            if hist.status == "running":
                hist.status = "cancelled"
                hist.cancelled = True
                hist.completed_at = datetime.now(timezone.utc)
                session.add(hist)
                session.commit()

        return self.get_execution_status(execution_id, profile_id=pid)

    def retry_execution(
        self,
        execution_id: str,
        *,
        profile_id: Optional[str] = None,
        allow_rerun_with_side_effects: bool = False,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely retry a failed/cancelled/interrupted execution after revalidating definition."""
        pid = self._resolve_profile_id(profile_id)
        prior = self.get_execution_status(execution_id, profile_id=pid)
        prior_status = prior.get("status")
        if prior_status not in ("failed", "cancelled", "interrupted"):
            raise AutomationValidationError(
                f"Cannot retry execution in '{prior_status}' state. Only failed, cancelled, or interrupted runs can be retried."
            )

        completed_steps = int(prior.get("completed_steps") or 0)
        if completed_steps > 0 and not allow_rerun_with_side_effects:
            raise AutomationValidationError(
                f"This run already completed {completed_steps} step(s). "
                "VoxPilot does not resume mid-workflow; confirm a full rerun from step 1 to avoid unintended duplicate actions."
            )

        # Revalidate automation ownership and definition before executing
        return self.execute_automation(
            prior["automation_id"],
            profile_id=pid,
            confirmation_token=confirmation_token,
            trigger_source="retry",
        )

    def delete_execution_history(self, execution_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            hist = session.get(AutomationExecutionRecord, execution_id)
            if hist is None:
                raise AutomationNotFoundError(f"Execution '{execution_id}' not found.")
            if hist.profile_id != pid:
                raise AutomationAccessError("Execution belongs to another profile.")
            if hist.status == "running":
                raise AutomationValidationError("Cannot delete a currently running execution. Cancel it first.")
            steps = session.exec(
                select(AutomationExecutionStepRecord).where(
                    AutomationExecutionStepRecord.execution_id == execution_id
                )
            ).all()
            for s in steps:
                session.delete(s)
            session.delete(hist)
            session.commit()

        with self._lock:
            self._executions.pop(execution_id, None)
            self._cancel_flags.pop(execution_id, None)
        return {"deleted": True, "execution_id": execution_id}

    def clear_execution_history(
        self,
        *,
        profile_id: Optional[str] = None,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        if not confirm:
            raise AutomationValidationError("Explicit confirmation is required to clear execution history.")
        pid = self._resolve_profile_id(profile_id)
        deleted_ids: List[str] = []
        with Session(self._get_engine()) as session:
            records = session.exec(
                select(AutomationExecutionRecord).where(
                    AutomationExecutionRecord.profile_id == pid,
                    AutomationExecutionRecord.status != "running",
                )
            ).all()
            for rec in records:
                deleted_ids.append(rec.execution_id)
                steps = session.exec(
                    select(AutomationExecutionStepRecord).where(
                        AutomationExecutionStepRecord.execution_id == rec.execution_id
                    )
                ).all()
                for s in steps:
                    session.delete(s)
                session.delete(rec)
            session.commit()

        with self._lock:
            for eid in deleted_ids:
                self._executions.pop(eid, None)
                self._cancel_flags.pop(eid, None)
        return {"cleared": True, "deleted_count": len(deleted_ids), "profile_id": pid}

    def mark_interrupted_executions(self) -> int:
        """Mark any leftover 'running' executions as 'interrupted' on shutdown."""
        now_dt = datetime.now(timezone.utc)
        updated = 0
        with Session(self._get_engine()) as session:
            running_recs = session.exec(
                select(AutomationExecutionRecord).where(AutomationExecutionRecord.status == "running")
            ).all()
            for rec in running_recs:
                rec.status = "interrupted"
                rec.error_summary = "VoxPilot shut down while execution was running."
                rec.completed_at = now_dt
                session.add(rec)
                updated += 1
            if updated:
                session.commit()
        return updated

    def run_due_schedules(self, now_local: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Evaluate enabled scheduled automations for the current local minute slot only.

        Missed past schedules (e.g. while VoxPilot was closed) are intentionally skipped
        rather than fired in a burst upon restart.
        """
        if get_shared_emergency_stop_manager().is_emergency_stop_active():
            return []

        now = now_local or datetime.now().astimezone()
        current_hhmm = now.strftime("%H:%M")
        current_slot = now.strftime("%Y-%m-%d %H:%M")
        current_weekday = now.weekday()

        due_items: List[tuple[str, str]] = []
        with Session(self._get_engine()) as session:
            stmt = select(AutomationRecord).where(
                AutomationRecord.enabled == True,  # noqa: E712
                AutomationRecord.trigger_type == "scheduled",
            )
            records = session.exec(stmt).all()
            for rec in records:
                try:
                    cfg = json.loads(rec.trigger_config_json or "{}")
                except Exception:
                    continue
                if cfg.get("paused"):
                    continue
                if cfg.get("time_of_day") != current_hhmm:
                    continue
                if cfg.get("frequency") == "weekly":
                    days = cfg.get("weekdays") or []
                    if current_weekday not in days:
                        continue
                if cfg.get("last_scheduled_slot") == current_slot:
                    continue
                cfg["last_scheduled_slot"] = current_slot
                rec.trigger_config_json = json.dumps(cfg)
                session.add(rec)
                due_items.append((rec.automation_id, rec.profile_id))
            if due_items:
                session.commit()

        results: List[Dict[str, Any]] = []
        for auto_id, pid in due_items:
            try:
                res = self.execute_automation(auto_id, profile_id=pid, trigger_source="scheduled")
                results.append(res)
            except Exception as err:
                logger.warning("Scheduled automation '%s' failed to start: %s", auto_id, err)
        return results

    def start_scheduler(self) -> None:
        with self._lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()

            def _loop() -> None:
                while not self._scheduler_stop.is_set():
                    try:
                        self.run_due_schedules()
                    except Exception as err:
                        logger.debug("Automation scheduler tick error: %s", err)
                    self._scheduler_stop.wait(20.0)

            self._scheduler_thread = threading.Thread(
                target=_loop, name="VoxPilot-Automation-Scheduler", daemon=True
            )
            self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        self._scheduler_stop.set()
        t = self._scheduler_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._scheduler_thread = None
        try:
            self.mark_interrupted_executions()
        except Exception:
            pass


_shared_custom_automation_service: Optional[CustomAutomationService] = None
_shared_custom_automation_lock = threading.Lock()


def get_shared_custom_automation_service() -> CustomAutomationService:
    global _shared_custom_automation_service
    with _shared_custom_automation_lock:
        if _shared_custom_automation_service is None:
            _shared_custom_automation_service = CustomAutomationService()
        return _shared_custom_automation_service
