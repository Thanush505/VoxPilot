"""
VoxPilot - Native Windows Desktop Notification Service (Phase 5C)

Provides local-first, privacy-preserving native Windows toast notifications
using Windows 10/11 WinRT (`Windows.UI.Notifications.ToastNotificationManager`)
via PowerShell without requiring external notification frameworks or cloud egress.

Security & Privacy Guarantees:
- Never transmits notifications externally.
- Redacts emails, file paths, API/auth tokens, and sensitive payloads.
- Uses generic safe messages for email, file, browser, and vision tasks.
- Never auto-approves confirmations or executes actions from toast text.
- Deduplicates overlapping event notifications with a bounded TTL cache.
- Respects per-profile preferences, active-use suppression, and EmergencyStopManager.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.paths import get_config_dir, is_frozen
from backend.profiles.service import get_shared_profile_service

logger = logging.getLogger(__name__)

# Built-in Windows PowerShell AppUserModelID works reliably in development mode
# without requiring Start Menu shortcut registration or administrator rights.
WINDOWS_POWERSHELL_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
VOXPILOT_PACKAGED_AUMID = "VoxPilot.DesktopAssistant"

NOTIFICATION_CATEGORIES: Dict[str, Dict[str, str]] = {
    "task_completed": {
        "label": "Task Completed",
        "description": "Notify when an agent task finishes successfully.",
    },
    "task_failed": {
        "label": "Task Failed",
        "description": "Notify when an agent task fails or cannot be completed.",
    },
    "long_running_task_completed": {
        "label": "Long-Running Task Completed",
        "description": "Notify when a multi-step or long-running task finishes.",
    },
    "confirmation_required": {
        "label": "Confirmation Required",
        "description": "Notify when VoxPilot needs your explicit approval to proceed.",
    },
    "automation_completed": {
        "label": "Automation Completed",
        "description": "Notify when a desktop, file, or browser automation finishes.",
    },
    "automation_failed": {
        "label": "Automation Failed",
        "description": "Notify when an automation action fails.",
    },
    "system_error": {
        "label": "Important System Error",
        "description": "Notify on critical backend, model, or hardware errors.",
    },
    "voice_error": {
        "label": "Voice Subsystem Error",
        "description": "Notify when microphone, speech-to-text, or voice mode fails.",
    },
}

DEFAULT_CATEGORY_PREFERENCES: Dict[str, bool] = {
    cat_id: True for cat_id in NOTIFICATION_CATEGORIES
}

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\s\"'<>|*?]+\\?)+")
_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?:Users|home|var|etc|tmp|opt|mnt)/[^\s\"']+")
_TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._\-]{8,}|(?:sk|pk|ghp|gho|AIza|xoxb|xoxp)[A-Za-z0-9_\-]{8,}|(?:password|passwd|secret|token|api_key)\s*[:=]\s*\S+)"
)
_URL_QUERY_RE = re.compile(r"https?://[^\s?#]+\?[^\s]+")

_SENSITIVE_KEYWORDS = (
    "email",
    "inbox",
    "gmail",
    "outlook",
    "password",
    "token",
    "secret",
    "resume",
    "document",
    "pdf",
    "screenshot",
    "screen",
    "clipboard",
    "microphone",
)


def sanitize_notification_text(text: str, max_length: int = 160) -> str:
    """Redact sensitive data (paths, emails, tokens, query URLs) and bound length."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    cleaned = _TOKEN_RE.sub("[redacted]", cleaned)
    cleaned = _EMAIL_RE.sub("[email]", cleaned)
    cleaned = _WIN_PATH_RE.sub("[local path]", cleaned)
    cleaned = _POSIX_PATH_RE.sub("[local path]", cleaned)
    cleaned = _URL_QUERY_RE.sub("[webpage]", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + "..."
    return cleaned


def contains_sensitive_context(text: str) -> bool:
    """Check whether text mentions sensitive domains that should use generic phrasing."""
    lower = (text or "").lower()
    return any(kw in lower for kw in _SENSITIVE_KEYWORDS)


def build_safe_notification_content(
    category: str,
    title: str,
    message: str,
    sensitive: bool = False,
) -> tuple[str, str]:
    """Return privacy-safe title and message for a notification category."""
    safe_title = sanitize_notification_text(title, max_length=72) or "VoxPilot"
    if sensitive or contains_sensitive_context(message):
        generic_by_category = {
            "task_completed": "Your VoxPilot task has finished. Open VoxPilot to view the result.",
            "long_running_task_completed": "Your long-running VoxPilot task has finished. Open VoxPilot to review.",
            "task_failed": "A VoxPilot task could not be completed. Open VoxPilot for details.",
            "confirmation_required": "VoxPilot requires your explicit confirmation in the app.",
            "automation_completed": "Your VoxPilot automation finished. Open VoxPilot to review.",
            "automation_failed": "A VoxPilot automation failed. Open VoxPilot to inspect.",
            "system_error": "VoxPilot encountered an important system error. Open VoxPilot to inspect.",
            "voice_error": "VoxPilot voice subsystem encountered an error. Open VoxPilot to check settings.",
        }
        return safe_title, generic_by_category.get(
            category,
            "Open VoxPilot to view the latest update.",
        )

    safe_message = sanitize_notification_text(message, max_length=160)
    if not safe_message:
        safe_message = "Open VoxPilot to view the latest update."
    return safe_title, safe_message


@dataclass
class NotificationDeliveryResult:
    delivered: bool
    suppressed: bool = False
    reason: Optional[str] = None
    category: str = "task_completed"
    title: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivered": self.delivered,
            "suppressed": self.suppressed,
            "reason": self.reason,
            "category": self.category,
            "title": self.title,
            "message": self.message,
        }


class NotificationService:
    """Manages native Windows desktop notifications and per-profile preferences."""

    DEDUP_TTL_SECONDS = 60.0
    ACTIVE_UI_WINDOW_SECONDS = 12.0

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        platform_override: Optional[str] = None,
        frozen_override: Optional[bool] = None,
        sender_fn: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._config_dir_override = config_dir
        self._platform_override = platform_override
        self._frozen_override = frozen_override
        self._sender_fn = sender_fn
        self._recent_events: Dict[str, float] = {}
        self._last_ui_activity_at: float = 0.0
        self._ui_window_active: bool = False
        self._last_delivery_error: Optional[str] = None

    def _get_platform(self) -> str:
        return self._platform_override if self._platform_override is not None else sys.platform

    def _is_supported(self) -> bool:
        return self._get_platform() == "win32"

    def _is_frozen_mode(self) -> bool:
        return self._frozen_override if self._frozen_override is not None else is_frozen()

    def _get_app_user_model_id(self) -> str:
        # In development mode (and packaged mode without a registered Start Menu shortcut AUMID),
        # Windows PowerShell's built-in AUMID guarantees native toast delivery without requiring
        # installer/registry modifications.
        if self._is_frozen_mode():
            return WINDOWS_POWERSHELL_AUMID
        return WINDOWS_POWERSHELL_AUMID

    def _resolve_profile_id(self, profile_id: Optional[str] = None) -> str:
        if profile_id and profile_id.strip():
            return re.sub(r"[^a-zA-Z0-9_-]", "_", profile_id.strip())
        try:
            active_id = get_shared_profile_service().get_active_profile_id()
            return re.sub(r"[^a-zA-Z0-9_-]", "_", active_id or "default")
        except Exception:
            return "default"

    def _prefs_path(self, profile_id: Optional[str] = None) -> Path:
        base_dir = self._config_dir_override if self._config_dir_override is not None else get_config_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        resolved_id = self._resolve_profile_id(profile_id)
        return base_dir / f"notifications_{resolved_id}.json"

    @staticmethod
    def default_preferences() -> Dict[str, Any]:
        return {
            "enabled": True,
            "suppress_when_active": True,
            "categories": dict(DEFAULT_CATEGORY_PREFERENCES),
        }

    def get_preferences(self, profile_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            defaults = self.default_preferences()
            path = self._prefs_path(profile_id)
            if not path.exists():
                return defaults
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    return defaults
                merged_cats = dict(DEFAULT_CATEGORY_PREFERENCES)
                raw_cats = raw.get("categories")
                if isinstance(raw_cats, dict):
                    for cat_id in DEFAULT_CATEGORY_PREFERENCES:
                        if cat_id in raw_cats:
                            merged_cats[cat_id] = bool(raw_cats[cat_id])
                return {
                    "enabled": bool(raw.get("enabled", defaults["enabled"])),
                    "suppress_when_active": bool(
                        raw.get("suppress_when_active", defaults["suppress_when_active"])
                    ),
                    "categories": merged_cats,
                }
            except Exception as err:
                logger.warning("Failed to load notification preferences: %s", err)
                return defaults

    def update_preferences(
        self,
        updates: Dict[str, Any],
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            current = self.get_preferences(profile_id)
            if "enabled" in updates and updates["enabled"] is not None:
                current["enabled"] = bool(updates["enabled"])
            if "suppress_when_active" in updates and updates["suppress_when_active"] is not None:
                current["suppress_when_active"] = bool(updates["suppress_when_active"])
            if isinstance(updates.get("categories"), dict):
                for cat_id, val in updates["categories"].items():
                    if cat_id in DEFAULT_CATEGORY_PREFERENCES:
                        current["categories"][cat_id] = bool(val)
            path = self._prefs_path(profile_id)
            path.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return current

    def reset_preferences(self, profile_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            defaults = self.default_preferences()
            path = self._prefs_path(profile_id)
            path.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
            return defaults

    def record_ui_activity(self, active: bool = True) -> None:
        with self._lock:
            self._ui_window_active = bool(active)
            self._last_ui_activity_at = time.monotonic() if active else 0.0

    def is_ui_recently_active(self) -> bool:
        with self._lock:
            if not self._ui_window_active or self._last_ui_activity_at <= 0.0:
                return False
            return (time.monotonic() - self._last_ui_activity_at) <= self.ACTIVE_UI_WINDOW_SECONDS

    def _is_duplicate_event(self, event_key: Optional[str]) -> bool:
        if not event_key:
            return False
        now = time.monotonic()
        expired = [k for k, ts in self._recent_events.items() if (now - ts) > self.DEDUP_TTL_SECONDS]
        for k in expired:
            del self._recent_events[k]
        if event_key in self._recent_events:
            return True
        self._recent_events[event_key] = now
        if len(self._recent_events) > 256:
            oldest_key = min(self._recent_events, key=self._recent_events.get)  # type: ignore[arg-type]
            del self._recent_events[oldest_key]
        return False

    def _send_windows_toast(self, title: str, message: str, app_id: str) -> None:
        """Deliver a native Windows toast notification via PowerShell WinRT."""
        xml_title = html.escape(title, quote=True)
        xml_body = html.escape(message, quote=True)
        # Pass title/body via base64-encoded UTF-16LE PowerShell script to prevent any injection.
        ps_script = (
            "$ErrorActionPreference = 'Stop';\n"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;\n"
            "$template = '<toast><visual><binding template=\"ToastGeneric\">"
            f"<text>{xml_title}</text><text>{xml_body}</text>"
            "</binding></visual></toast>';\n"
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;\n"
            "$xml.LoadXml($template);\n"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);\n"
            f"$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}');\n"
            "$notifier.Show($toast);\n"
        )
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=creationflags,
        )
        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
            raise RuntimeError(err_msg)

    def notify(
        self,
        category: str,
        title: str,
        message: str,
        *,
        event_key: Optional[str] = None,
        profile_id: Optional[str] = None,
        sensitive: bool = False,
        force: bool = False,
    ) -> NotificationDeliveryResult:
        """Send a native desktop notification if supported, enabled, and not suppressed."""
        if category not in NOTIFICATION_CATEGORIES:
            category = "task_completed"

        safe_title, safe_message = build_safe_notification_content(
            category=category,
            title=title,
            message=message,
            sensitive=sensitive,
        )

        if not self._is_supported():
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=True,
                reason="unsupported_platform",
                category=category,
                title=safe_title,
                message=safe_message,
            )

        if not force and get_shared_emergency_stop_manager().is_emergency_stop_active():
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=True,
                reason="emergency_stop_active",
                category=category,
                title=safe_title,
                message=safe_message,
            )

        prefs = self.get_preferences(profile_id)
        if not force and not prefs.get("enabled", True):
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=True,
                reason="notifications_disabled",
                category=category,
                title=safe_title,
                message=safe_message,
            )

        if not force and not prefs.get("categories", {}).get(category, True):
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=True,
                reason="category_disabled",
                category=category,
                title=safe_title,
                message=safe_message,
            )

        if not force and prefs.get("suppress_when_active", True) and self.is_ui_recently_active():
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=True,
                reason="suppressed_while_active",
                category=category,
                title=safe_title,
                message=safe_message,
            )

        with self._lock:
            if not force and self._is_duplicate_event(event_key):
                return NotificationDeliveryResult(
                    delivered=False,
                    suppressed=True,
                    reason="duplicate_event",
                    category=category,
                    title=safe_title,
                    message=safe_message,
                )

        app_id = self._get_app_user_model_id()
        try:
            if self._sender_fn is not None:
                self._sender_fn(safe_title, safe_message, app_id)
            else:
                self._send_windows_toast(safe_title, safe_message, app_id)
            with self._lock:
                self._last_delivery_error = None
            return NotificationDeliveryResult(
                delivered=True,
                suppressed=False,
                reason=None,
                category=category,
                title=safe_title,
                message=safe_message,
            )
        except Exception as err:
            logger.warning("Desktop notification delivery failed: %s", err)
            with self._lock:
                self._last_delivery_error = str(err)
            return NotificationDeliveryResult(
                delivered=False,
                suppressed=False,
                reason=f"delivery_failed: {err}",
                category=category,
                title=safe_title,
                message=safe_message,
            )

    def send_test_notification(self, profile_id: Optional[str] = None) -> NotificationDeliveryResult:
        """Send a safe preview toast notification regardless of active-UI suppression."""
        return self.notify(
            category="task_completed",
            title="VoxPilot Notifications Active",
            message="Desktop notifications are working for your active VoxPilot profile.",
            profile_id=profile_id,
            force=True,
        )

    def get_status(self, profile_id: Optional[str] = None) -> Dict[str, Any]:
        resolved_profile = self._resolve_profile_id(profile_id)
        prefs = self.get_preferences(resolved_profile)
        supported = self._is_supported()
        mode = "packaged" if self._is_frozen_mode() else "development"
        reason: Optional[str] = None
        if not supported:
            reason = "Native Windows toast notifications are only supported on Windows."
        elif self._last_delivery_error:
            reason = self._last_delivery_error

        return {
            "supported": supported,
            "platform": self._get_platform(),
            "mode": mode,
            "mechanism": "windows_winrt_toast",
            "app_user_model_id": self._get_app_user_model_id(),
            "active_profile_id": resolved_profile,
            "ui_recently_active": self.is_ui_recently_active(),
            "last_error": self._last_delivery_error,
            "reason": reason,
            "preferences": prefs,
            "categories_meta": [
                {"id": cat_id, "label": meta["label"], "description": meta["description"]}
                for cat_id, meta in NOTIFICATION_CATEGORIES.items()
            ],
        }


_shared_notification_service: Optional[NotificationService] = None
_shared_notification_lock = threading.Lock()


def get_shared_notification_service() -> NotificationService:
    global _shared_notification_service
    with _shared_notification_lock:
        if _shared_notification_service is None:
            _shared_notification_service = NotificationService()
        return _shared_notification_service
