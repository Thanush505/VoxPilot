"""
VoxPilot - Phase 5C: Native Desktop Notifications Tests.

Covers:
- Notification service initialization and Windows capability detection
- Enabled/disabled master and per-category preferences
- Task completion, task failure, and confirmation-required outcome notifications
- Duplicate event prevention via event_key TTL cache
- Sensitive-content redaction (emails, tokens, passwords, file paths, sensitive intents)
- Profile preference isolation
- Unsupported platform behavior and delivery failure graceful fallback
- EmergencyStopManager and cancelled execution suppression
- FastAPI notification endpoints and settings persistence
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.agent.graph import VoxPilotAgent
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.automation.notifications import (
    NOTIFICATION_CATEGORIES,
    NotificationService,
    build_safe_notification_content,
    sanitize_notification_text,
)
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_service(
    tmp_path: Path,
    platform: str = "win32",
    frozen: bool = False,
    sender_fn=None,
) -> tuple[NotificationService, list[tuple[str, str, str]]]:
    sent: list[tuple[str, str, str]] = []

    def _mock_sender(title: str, msg: str, app_id: str) -> None:
        if sender_fn is not None:
            sender_fn(title, msg, app_id)
        sent.append((title, msg, app_id))

    svc = NotificationService(
        config_dir=tmp_path,
        platform_override=platform,
        frozen_override=frozen,
        sender_fn=_mock_sender,
    )
    return svc, sent


def test_notification_service_initialization_and_defaults(tmp_path: Path):
    svc, _ = _make_service(tmp_path)
    status = svc.get_status(profile_id="default")
    assert status["supported"] is True
    assert status["platform"] == "win32"
    assert status["mode"] == "development"
    assert status["mechanism"] == "windows_winrt_toast"
    assert status["preferences"]["enabled"] is True
    assert status["preferences"]["suppress_when_active"] is True
    for cat_id in NOTIFICATION_CATEGORIES:
        assert status["preferences"]["categories"][cat_id] is True


def test_notification_enabled_disabled_and_category_toggles(tmp_path: Path):
    svc, sent = _make_service(tmp_path)

    # Disable globally -> suppressed
    svc.update_preferences({"enabled": False}, profile_id="default")
    res_disabled = svc.notify("task_completed", "Done", "Finished!", profile_id="default")
    assert res_disabled.delivered is False
    assert res_disabled.reason == "notifications_disabled"
    assert len(sent) == 0

    # Re-enable globally, disable only task_failed
    svc.update_preferences({"enabled": True, "categories": {"task_failed": False}}, profile_id="default")
    res_cat_off = svc.notify("task_failed", "Failed", "Error happened", profile_id="default")
    assert res_cat_off.delivered is False
    assert res_cat_off.reason == "category_disabled"

    res_ok = svc.notify("task_completed", "Done", "All good", profile_id="default")
    assert res_ok.delivered is True
    assert len(sent) == 1


def test_agent_outcome_notifications_completed_failed_confirmation(tmp_path: Path):
    svc, sent = _make_service(tmp_path)
    with patch("backend.automation.notifications.get_shared_notification_service", return_value=svc):
        # 1. Task completed
        VoxPilotAgent._notify_execution_outcome({
            "execution_id": "exec-1",
            "profile_id": "default",
            "execution_status": "completed",
            "intent": "general_chat",
            "plan": [],
            "response": "Hardware check complete.",
        })
        assert len(sent) == 1
        assert "Completed" in sent[-1][0]

        # 2. Task failed
        VoxPilotAgent._notify_execution_outcome({
            "execution_id": "exec-2",
            "profile_id": "default",
            "execution_status": "failed",
            "intent": "general_chat",
            "plan": [],
            "error": "Model timeout occurred.",
        })
        assert len(sent) == 2
        assert "Failed" in sent[-1][0]

        # 3. Confirmation required (always generic & never auto-approves)
        VoxPilotAgent._notify_execution_outcome({
            "execution_id": "exec-3",
            "profile_id": "default",
            "execution_status": "confirmation_required",
            "intent": "desktop_control",
            "plan": [{"step": 1}],
        })
        assert len(sent) == 3
        assert "Confirmation" in sent[-1][0]
        assert "approval" in sent[-1][1].lower() or "confirmation" in sent[-1][1].lower()


def test_duplicate_event_prevention(tmp_path: Path):
    svc, sent = _make_service(tmp_path)
    r1 = svc.notify("task_completed", "Done", "Task 1 finished", event_key="exec:42:completed")
    r2 = svc.notify("task_completed", "Done", "Task 1 finished", event_key="exec:42:completed")
    assert r1.delivered is True
    assert r2.delivered is False
    assert r2.reason == "duplicate_event"
    assert len(sent) == 1


def test_sensitive_content_redaction():
    raw = (
        "Sent email to alice@example.com with bearer sk-secret1234567890 "
        "from C:\\Users\\Thanu\\Documents\\resume.pdf and https://mail.example.com/inbox?token=abc"
    )
    sanitized = sanitize_notification_text(raw)
    assert "alice@example.com" not in sanitized
    assert "sk-secret1234567890" not in sanitized
    assert "resume.pdf" not in sanitized
    assert "token=abc" not in sanitized

    _, generic = build_safe_notification_content(
        category="task_completed",
        title="VoxPilot Task Completed",
        message="Summarized your email from boss@company.com about salary",
        sensitive=True,
    )
    assert "boss@company.com" not in generic
    assert "salary" not in generic
    assert "Open VoxPilot" in generic


def test_profile_preference_isolation(tmp_path: Path):
    svc, sent = _make_service(tmp_path)
    svc.update_preferences({"enabled": False}, profile_id="work")
    svc.update_preferences({"enabled": True}, profile_id="personal")

    assert svc.get_preferences("work")["enabled"] is False
    assert svc.get_preferences("personal")["enabled"] is True

    r_work = svc.notify("task_completed", "Done", "Work task", profile_id="work")
    r_personal = svc.notify("task_completed", "Done", "Personal task", profile_id="personal")
    assert r_work.delivered is False
    assert r_personal.delivered is True
    assert len(sent) == 1


def test_unsupported_platform_and_delivery_failure(tmp_path: Path):
    # Unsupported OS
    svc_linux, sent_linux = _make_service(tmp_path, platform="linux")
    res_linux = svc_linux.notify("task_completed", "Done", "Hello")
    assert res_linux.delivered is False
    assert res_linux.reason == "unsupported_platform"
    assert len(sent_linux) == 0

    # Delivery exception caught gracefully
    def _boom(_t, _m, _a):
        raise RuntimeError("Toast service unavailable")

    svc_err, _ = _make_service(tmp_path, platform="win32", sender_fn=_boom)
    res_err = svc_err.notify("task_completed", "Done", "Hello")
    assert res_err.delivered is False
    assert "delivery_failed" in (res_err.reason or "")
    assert svc_err.get_status()["last_error"] == "Toast service unavailable"


def test_emergency_stop_and_cancelled_execution_suppression(tmp_path: Path):
    svc, sent = _make_service(tmp_path)

    # Cancelled execution produces no notification
    with patch("backend.automation.notifications.get_shared_notification_service", return_value=svc):
        VoxPilotAgent._notify_execution_outcome({
            "execution_id": "exec-cancel",
            "profile_id": "default",
            "execution_status": "cancelled",
            "intent": "desktop_control",
            "plan": [{"step": 1}],
        })
    assert len(sent) == 0

    # Emergency stop blocks notifications
    mgr = get_shared_emergency_stop_manager()
    mgr.trigger_emergency_stop(reason="Test emergency halt")
    res = svc.notify("task_completed", "Done", "Should not notify")
    assert res.delivered is False
    assert res.reason == "emergency_stop_active"
    assert len(sent) == 0


def test_active_ui_suppression_and_test_notification_bypass(tmp_path: Path):
    svc, sent = _make_service(tmp_path)
    svc.record_ui_activity(True)

    res_suppressed = svc.notify("task_completed", "Done", "Background update")
    assert res_suppressed.delivered is False
    assert res_suppressed.reason == "suppressed_while_active"

    # Explicit test notification bypasses active-UI suppression
    res_test = svc.send_test_notification()
    assert res_test.delivered is True
    assert len(sent) == 1


def test_fastapi_notification_endpoints_and_persistence(tmp_path: Path):
    svc, sent = _make_service(tmp_path)
    client = TestClient(app)

    with patch("backend.api.system.get_shared_notification_service", return_value=svc):
        r_get = client.get("/api/system/notifications")
        assert r_get.status_code == 200
        assert r_get.json()["preferences"]["enabled"] is True

        r_post = client.post(
            "/api/system/notifications",
            json={"enabled": True, "suppress_when_active": False, "categories": {"voice_error": False}},
        )
        assert r_post.status_code == 200
        assert r_post.json()["preferences"]["suppress_when_active"] is False
        assert r_post.json()["preferences"]["categories"]["voice_error"] is False

        r_test = client.post("/api/system/notifications/test")
        assert r_test.status_code == 200
        assert r_test.json()["delivered"] is True
        assert len(sent) == 1

        r_act = client.post("/api/system/notifications/activity", json={"active": True})
        assert r_act.status_code == 200
        assert svc.is_ui_recently_active() is True

        r_reset = client.post("/api/system/notifications/reset")
        assert r_reset.status_code == 200
        assert r_reset.json()["preferences"]["suppress_when_active"] is True
        assert r_reset.json()["preferences"]["categories"]["voice_error"] is True
