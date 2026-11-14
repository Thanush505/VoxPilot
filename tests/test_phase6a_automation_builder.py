"""
VoxPilot - Phase 6A: Custom Automation Builder Tests.

Covers:
- Natural-language automation parsing & preview (without auto-saving or auto-executing)
- Structured automation validation, forbidden shell actions, and invalid arguments
- Automation creation, editing, toggle, and deletion
- Strict profile isolation
- Manual ordered action execution via ToolExecutor
- Step failure handling, cancellation, and EmergencyStopManager integration
- Destructive-action confirmation enforcement (delete_file)
- Schedule persistence, duplicate-slot prevention, and missed-run skipping
- FastAPI endpoints and trusted-origin authorization
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from backend.automation.custom_automations import (
    AutomationAccessError,
    AutomationValidationError,
    CustomAutomationService,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_service(tmp_path: Path, executor=None) -> CustomAutomationService:
    db_path = tmp_path / "phase6a_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return CustomAutomationService(engine=engine, tool_executor=executor)


def test_nl_preview_parses_without_saving_or_executing(tmp_path: Path):
    svc = _make_service(tmp_path)
    preview = svc.preview_automation(
        "Create an automation that opens Chrome every morning at 9 AM, then opens VS Code"
    )
    assert preview["requires_approval"] is True
    assert preview["trigger_type"] == "scheduled"
    assert preview["trigger_config"]["frequency"] == "daily"
    assert preview["trigger_config"]["time_of_day"] == "09:00"
    actions = [s["action"] for s in preview["steps"]]
    assert actions == ["launch_application", "launch_application"]
    assert preview["steps"][0]["parameters"]["application_name"] == "Google Chrome"
    assert preview["steps"][1]["parameters"]["application_name"] == "Visual Studio Code"
    # Preview must NOT auto-save
    assert svc.list_automations(profile_id="default") == []


def test_validation_blocks_unsupported_actions_secrets_and_invalid_args(tmp_path: Path):
    svc = _make_service(tmp_path)

    # Forbidden shell action
    with pytest.raises(AutomationValidationError):
        svc.create_automation(
            name="Bad Shell",
            steps=[{"action": "execute_shell", "parameters": {"cmd": "whoami"}}],
            profile_id="default",
        )

    # Unknown tool
    with pytest.raises(AutomationValidationError):
        svc.create_automation(
            name="Unknown Tool",
            steps=[{"action": "nonexistent_tool_xyz", "parameters": {}}],
            profile_id="default",
        )

    # Invalid arguments (missing required application_name)
    with pytest.raises(AutomationValidationError):
        svc.create_automation(
            name="Bad Args",
            steps=[{"action": "launch_application", "parameters": {}}],
            profile_id="default",
        )

    # Secret in definition rejected
    with pytest.raises(AutomationValidationError):
        svc.create_automation(
            name="Secret Leak",
            steps=[{"action": "type_text", "parameters": {"text": "sk-supersecret1234567890"}}],
            profile_id="default",
        )


def test_crud_editing_and_profile_isolation(tmp_path: Path):
    svc = _make_service(tmp_path)
    created = svc.create_automation(
        name="Morning Dev",
        description="Launch tools",
        trigger_type="manual",
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="work",
    )
    auto_id = created["automation_id"]

    # Work profile can list & edit
    assert len(svc.list_automations(profile_id="work")) == 1
    assert svc.list_automations(profile_id="personal") == []

    updated = svc.update_automation(auto_id, {"name": "Morning Dev Updated", "enabled": False}, profile_id="work")
    assert updated["name"] == "Morning Dev Updated"
    assert updated["enabled"] is False

    # Personal profile cannot read, edit, execute, or delete work's automation
    with pytest.raises(AutomationAccessError):
        svc.get_automation(auto_id, profile_id="personal")
    with pytest.raises(AutomationAccessError):
        svc.update_automation(auto_id, {"name": "Hijack"}, profile_id="personal")
    with pytest.raises(AutomationAccessError):
        svc.execute_automation(auto_id, profile_id="personal")
    with pytest.raises(AutomationAccessError):
        svc.delete_automation(auto_id, profile_id="personal")

    deleted = svc.delete_automation(auto_id, profile_id="work")
    assert deleted["deleted"] is True


def test_ordered_execution_and_step_failure_stops_safely(tmp_path: Path):
    calls: list[str] = []

    class _FakeExecutor:
        def execute_step(self, step, step_index=0, override_confirmation_token=None):
            calls.append(step["action"])
            if step_index == 1:
                return {"success": False, "status": "failed", "error": "App failed to launch"}
            return {"success": True, "status": "completed", "message": "OK"}

    svc = _make_service(tmp_path, executor=_FakeExecutor())
    created = svc.create_automation(
        name="Two Step",
        steps=[
            {"action": "get_system_info", "parameters": {}},
            {"action": "launch_application", "parameters": {"application_name": "Notepad"}},
            {"action": "get_system_info", "parameters": {}},
        ],
        profile_id="default",
    )
    res = svc.execute_automation(created["automation_id"], profile_id="default")
    assert res["status"] == "failed"
    assert calls == ["get_system_info", "launch_application"]
    assert res["steps"][0]["status"] == "completed"
    assert res["steps"][1]["status"] == "failed"
    assert res["steps"][2]["status"] == "pending"


def test_destructive_action_requires_confirmation(tmp_path: Path):
    svc = _make_service(tmp_path)
    created = svc.create_automation(
        name="Delete Temp",
        steps=[{"action": "delete_file", "parameters": {"file_path": str(tmp_path / "demo.txt")}}],
        profile_id="default",
    )
    res = svc.execute_automation(created["automation_id"], profile_id="default")
    assert res["status"] == "confirmation_required"
    assert res["steps"][0]["status"] == "confirmation_required"
    assert res["pending_confirmation"] is not None


def test_cancellation_and_emergency_stop(tmp_path: Path):
    svc = _make_service(tmp_path)
    created = svc.create_automation(
        name="Simple Check",
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="default",
    )

    # Emergency stop blocks execution
    mgr = get_shared_emergency_stop_manager()
    mgr.trigger_emergency_stop(reason="Test emergency")
    with pytest.raises(AutomationValidationError):
        svc.execute_automation(created["automation_id"], profile_id="default")
    mgr.reset_emergency_stop()

    # Cancel active execution record
    exec_res = svc.execute_automation(created["automation_id"], profile_id="default")
    with svc._lock:
        svc._executions[exec_res["execution_id"]]["status"] = "running"
    cancelled = svc.cancel_execution(exec_res["execution_id"], profile_id="default")
    assert cancelled["status"] == "cancelled"


def test_schedule_persistence_duplicate_prevention_and_missed_skip(tmp_path: Path):
    fake_exec = MagicMock()
    fake_exec.execute_step.return_value = {"success": True, "status": "completed", "message": "OK"}
    svc = _make_service(tmp_path, executor=fake_exec)

    created = svc.create_automation(
        name="Daily 9AM",
        trigger_type="scheduled",
        trigger_config={"frequency": "daily", "time_of_day": "09:00"},
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="default",
    )

    # Missed slot (09:05) -> skipped, never fires retroactively
    missed_runs = svc.run_due_schedules(now_local=datetime(2026, 11, 15, 9, 5))
    assert missed_runs == []

    # Exact slot (09:00) -> fires once
    due_runs = svc.run_due_schedules(now_local=datetime(2026, 11, 15, 9, 0))
    assert len(due_runs) == 1

    # Duplicate tick in same minute slot -> prevented
    dup_runs = svc.run_due_schedules(now_local=datetime(2026, 11, 15, 9, 0))
    assert dup_runs == []

    # Pause schedule -> does not fire next day
    svc.update_schedule(created["automation_id"], {"frequency": "daily", "time_of_day": "09:00", "paused": True}, profile_id="default")
    paused_runs = svc.run_due_schedules(now_local=datetime(2026, 11, 16, 9, 0))
    assert paused_runs == []


def test_fastapi_automation_endpoints_and_authorization(tmp_path: Path):
    fake_exec = MagicMock()
    fake_exec.execute_step.return_value = {"success": True, "status": "completed", "message": "OK"}
    svc = _make_service(tmp_path, executor=fake_exec)
    client = TestClient(app)

    # Untrusted Origin rejected
    r_untrusted = client.get("/api/automations", headers={"Origin": "https://evil.example.com"})
    assert r_untrusted.status_code == 403

    with patch("backend.api.automations.get_shared_custom_automation_service", return_value=svc):
        r_prev = client.post("/api/automations/preview", json={"prompt": "Open Notepad"})
        assert r_prev.status_code == 200
        assert r_prev.json()["requires_approval"] is True

        r_create = client.post(
            "/api/automations",
            json={
                "name": "API Check",
                "description": "Test",
                "trigger_type": "manual",
                "trigger_config": {},
                "steps": [{"action": "get_system_info", "parameters": {}}],
            },
        )
        assert r_create.status_code == 200
        auto_id = r_create.json()["automation_id"]

        r_exec = client.post(f"/api/automations/{auto_id}/execute")
        assert r_exec.status_code == 200
        assert r_exec.json()["status"] == "completed"

        r_del = client.delete(f"/api/automations/{auto_id}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True
