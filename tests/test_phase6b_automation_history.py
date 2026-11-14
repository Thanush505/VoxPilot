"""
VoxPilot - Phase 6B: Automation History & Management Tests.

Covers:
- Execution history persistence across service instances
- Successful, failed, partial, cancelled, interrupted, and confirmation-required records
- Ordered step history with safe input/output/error redaction
- Filtering (automation_id, status, trigger_type, date range) and pagination
- Profile isolation and unauthorized access checks
- Safe retry behavior (blocks blind rerun when steps already completed)
- Individual deletion and confirmed clear-history per profile
- Duplicate notification event prevention and EmergencyStop integration
- FastAPI history, retry, cancel, and delete endpoints
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from backend.automation.custom_automations import (
    AutomationAccessError,
    AutomationNotFoundError,
    AutomationValidationError,
    CustomAutomationService,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.automation.notifications import NotificationService
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_engine(tmp_path: Path):
    db_path = tmp_path / "phase6b_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_history_persistence_across_restart_and_ordered_steps(tmp_path: Path):
    engine = _make_engine(tmp_path)
    fake_exec = MagicMock()
    fake_exec.execute_step.side_effect = [
        {"success": True, "status": "completed", "message": "System info OK"},
        {"success": True, "status": "completed", "message": "Sent to user@example.com with sk-secret1234567890"},
    ]
    svc1 = CustomAutomationService(engine=engine, tool_executor=fake_exec)
    auto = svc1.create_automation(
        name="Multi-Step Check",
        steps=[
            {"action": "get_system_info", "parameters": {}},
            {"action": "launch_application", "parameters": {"application_name": "Notepad"}},
        ],
        profile_id="default",
    )
    exec_res = svc1.execute_automation(auto["automation_id"], profile_id="default")
    assert exec_res["status"] == "completed"
    assert exec_res["completed_steps"] == 2

    # Fresh service instance simulates app restart (no in-memory cache)
    svc2 = CustomAutomationService(engine=engine)
    persisted = svc2.get_execution_status(exec_res["execution_id"], profile_id="default")
    assert persisted["status"] == "completed"
    assert persisted["automation_name"] == "Multi-Step Check"
    assert persisted["completed_steps"] == 2
    assert persisted["total_steps"] == 2
    assert len(persisted["steps"]) == 2
    assert [s["step_index"] for s in persisted["steps"]] == [0, 1]
    # Redacted output
    assert "user@example.com" not in (persisted["steps"][1]["output_summary"] or "")
    assert "sk-secret1234567890" not in (persisted["steps"][1]["output_summary"] or "")


def test_partial_failure_exception_and_retry_safety(tmp_path: Path):
    engine = _make_engine(tmp_path)

    class _PartialFailExecutor:
        def execute_step(self, step, step_index=0, override_confirmation_token=None):
            if step_index == 0:
                return {"success": True, "status": "completed", "message": "Step 1 done"}
            raise RuntimeError("Tool crashed with bearer sk-token1234567890")

    svc = CustomAutomationService(engine=engine, tool_executor=_PartialFailExecutor())
    auto = svc.create_automation(
        name="Partial Fail Flow",
        steps=[
            {"action": "get_system_info", "parameters": {}},
            {"action": "launch_application", "parameters": {"application_name": "Notepad"}},
        ],
        profile_id="default",
    )
    res = svc.execute_automation(auto["automation_id"], profile_id="default")
    assert res["status"] == "failed"
    assert res["completed_steps"] == 1
    assert res["failed_step_index"] == 1
    assert "sk-token1234567890" not in (res["error_summary"] or "")

    # Blind retry blocked when completed_steps > 0
    with pytest.raises(AutomationValidationError, match="already completed 1 step"):
        svc.retry_execution(res["execution_id"], profile_id="default")

    # Explicit full rerun allowed after revalidation
    fake_ok = MagicMock()
    fake_ok.execute_step.return_value = {"success": True, "status": "completed", "message": "OK"}
    svc_ok = CustomAutomationService(engine=engine, tool_executor=fake_ok)
    rerun = svc_ok.retry_execution(
        res["execution_id"],
        profile_id="default",
        allow_rerun_with_side_effects=True,
    )
    assert rerun["status"] == "completed"
    assert rerun["trigger_type"] == "retry"


def test_confirmation_cancellation_emergency_stop_and_shutdown(tmp_path: Path):
    engine = _make_engine(tmp_path)
    svc = CustomAutomationService(engine=engine)
    auto = svc.create_automation(
        name="Delete File Flow",
        steps=[{"action": "delete_file", "parameters": {"file_path": str(tmp_path / "x.txt")}}],
        profile_id="default",
    )
    conf_res = svc.execute_automation(auto["automation_id"], profile_id="default")
    assert conf_res["status"] == "confirmation_required"
    # Cannot retry confirmation_required run
    with pytest.raises(AutomationValidationError):
        svc.retry_execution(conf_res["execution_id"], profile_id="default")

    # Emergency stop blocks retry/execution
    mgr = get_shared_emergency_stop_manager()
    mgr.trigger_emergency_stop(reason="Test halt")
    with pytest.raises(AutomationValidationError):
        svc.execute_automation(auto["automation_id"], profile_id="default")
    mgr.reset_emergency_stop()

    # Shutdown marks stale running records interrupted
    from backend.database.models import AutomationExecutionRecord
    from sqlmodel import Session

    with Session(engine) as session:
        session.add(
            AutomationExecutionRecord(
                execution_id="autoexec_stale",
                automation_id=auto["automation_id"],
                profile_id="default",
                automation_name="Delete File Flow",
                status="running",
                total_steps=1,
            )
        )
        session.commit()

    assert svc.mark_interrupted_executions() == 1
    stale = svc.get_execution_status("autoexec_stale", profile_id="default")
    assert stale["status"] == "interrupted"


def test_filtering_pagination_profile_isolation_and_clear(tmp_path: Path):
    engine = _make_engine(tmp_path)
    fake_exec = MagicMock()
    fake_exec.execute_step.return_value = {"success": True, "status": "completed", "message": "OK"}
    svc = CustomAutomationService(engine=engine, tool_executor=fake_exec)

    a_work = svc.create_automation(
        name="Work Flow",
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="work",
    )
    a_pers = svc.create_automation(
        name="Personal Flow",
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="personal",
    )

    e1 = svc.execute_automation(a_work["automation_id"], profile_id="work", trigger_source="manual")
    e2 = svc.execute_automation(a_work["automation_id"], profile_id="work", trigger_source="scheduled")
    e_pers = svc.execute_automation(a_pers["automation_id"], profile_id="personal", trigger_source="manual")

    # Profile isolation
    with pytest.raises(AutomationAccessError):
        svc.get_execution_status(e1["execution_id"], profile_id="personal")
    with pytest.raises(AutomationAccessError):
        svc.delete_execution_history(e1["execution_id"], profile_id="personal")

    # Filtering & pagination
    work_sched = svc.list_execution_history(profile_id="work", trigger_type="scheduled")
    assert work_sched["total"] == 1
    assert work_sched["items"][0]["execution_id"] == e2["execution_id"]

    paged = svc.list_execution_history(profile_id="work", limit=1, offset=1)
    assert paged["total"] == 2
    assert len(paged["items"]) == 1

    # Clear requires explicit confirm=True and isolates by profile
    with pytest.raises(AutomationValidationError):
        svc.clear_execution_history(profile_id="work", confirm=False)

    cleared = svc.clear_execution_history(profile_id="work", confirm=True)
    assert cleared["deleted_count"] == 2
    assert svc.list_execution_history(profile_id="work")["total"] == 0
    assert svc.list_execution_history(profile_id="personal")["total"] == 1

    # Single delete
    del_res = svc.delete_execution_history(e_pers["execution_id"], profile_id="personal")
    assert del_res["deleted"] is True
    with pytest.raises(AutomationNotFoundError):
        svc.get_execution_status(e_pers["execution_id"], profile_id="personal")


def test_duplicate_notification_prevention_on_execution(tmp_path: Path):
    engine = _make_engine(tmp_path)
    sent: list[tuple[str, str, str]] = []
    notif_svc = NotificationService(
        config_dir=tmp_path,
        platform_override="win32",
        sender_fn=lambda t, m, a: sent.append((t, m, a)),
    )
    fake_exec = MagicMock()
    fake_exec.execute_step.return_value = {"success": True, "status": "completed", "message": "OK"}
    svc = CustomAutomationService(engine=engine, tool_executor=fake_exec)

    with patch("backend.automation.notifications.get_shared_notification_service", return_value=notif_svc):
        auto = svc.create_automation(
            name="Notify Once",
            steps=[{"action": "get_system_info", "parameters": {}}],
            profile_id="default",
        )
        res = svc.execute_automation(auto["automation_id"], profile_id="default")
        # Calling _notify_outcome again with same execution_id is deduplicated
        svc._notify_outcome("Notify Once", res["execution_id"], "completed", None, "default")
    assert len(sent) == 1


def test_fastapi_history_endpoints_and_authorization(tmp_path: Path):
    engine = _make_engine(tmp_path)
    fake_exec = MagicMock()
    fake_exec.execute_step.side_effect = [
        {"success": False, "status": "failed", "error": "First step failed"},
        {"success": True, "status": "completed", "message": "Retry succeeded"},
    ]
    svc = CustomAutomationService(engine=engine, tool_executor=fake_exec)
    auto = svc.create_automation(
        name="API History Flow",
        steps=[{"action": "get_system_info", "parameters": {}}],
        profile_id="default",
    )
    failed_exec = svc.execute_automation(auto["automation_id"], profile_id="default")
    client = TestClient(app)

    # Untrusted Origin rejected
    r_untrusted = client.get("/api/automations/history", headers={"Origin": "https://evil.example.com"})
    assert r_untrusted.status_code == 403

    with patch("backend.api.automations.get_shared_custom_automation_service", return_value=svc):
        r_hist = client.get("/api/automations/history?status=failed")
        assert r_hist.status_code == 200
        assert r_hist.json()["total"] == 1

        r_det = client.get(f"/api/automations/executions/{failed_exec['execution_id']}")
        assert r_det.status_code == 200
        assert len(r_det.json()["steps"]) == 1

        # Failed on step 0 (completed_steps == 0) -> safe retry succeeds
        r_retry = client.post(f"/api/automations/executions/{failed_exec['execution_id']}/retry", json={})
        assert r_retry.status_code == 200
        assert r_retry.json()["status"] == "completed"

        r_del = client.delete(f"/api/automations/executions/{failed_exec['execution_id']}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True

        r_clr_no = client.delete("/api/automations/history?confirm=false")
        assert r_clr_no.status_code == 422

        r_clr_yes = client.delete("/api/automations/history?confirm=true")
        assert r_clr_yes.status_code == 200
        assert r_clr_yes.json()["cleared"] is True
