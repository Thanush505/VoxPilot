"""
Unit and integration tests for Emergency Stop Safe Reset and Operator Recovery.
Tests strict rejection of unverified resets, removal of unrestricted force=True,
and verification requirements for operator emergency recovery.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    UnsafeResetError,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
)


@pytest.fixture(autouse=True)
def clean_manager():
    """Ensure a fresh EmergencyStopManager for each test."""
    manager = EmergencyStopManager()
    set_shared_emergency_stop_manager(manager)
    yield manager
    if manager.is_emergency_stop_active():
        for op in manager.list_active_operations():
            manager.unregister_active_operation(op["operation_id"])
        manager.reset_emergency_stop()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestEmergencyStopResetSafety:
    """Verifies that normal reset strictly prohibits resetting while operations are active."""

    def test_safe_reset_succeeds_when_no_active_operations(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.trigger_emergency_stop(reason="Test trigger", source="test")
        assert manager.is_emergency_stop_active() is True

        status = manager.reset_emergency_stop()
        assert status["is_active"] is False
        assert not manager.is_emergency_stop_active()
        assert manager.reset_at is not None

    def test_safe_reset_fails_when_active_operations_remain(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_typing", execution_id="exec_1", category="keyboard")
        manager.trigger_emergency_stop(reason="User stopped loop", source="hotkey")

        with pytest.raises(UnsafeResetError) as exc_info:
            manager.reset_emergency_stop()

        assert "active operations have not reached a confirmed safe terminal state" in str(exc_info.value)
        assert exc_info.value.active_count == 1
        assert "op_typing" in exc_info.value.active_operations
        assert manager.is_emergency_stop_active() is True

    def test_force_true_parameter_is_disallowed(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_browser", execution_id="exec_2", category="browser")
        manager.trigger_emergency_stop(reason="Navigation stall", source="api")

        with pytest.raises(UnsafeResetError) as exc_info:
            manager.reset_emergency_stop(force=True)

        assert "Unrestricted 'force=True' reset has been removed for safety" in str(exc_info.value)
        assert manager.is_emergency_stop_active() is True


class TestOperatorEmergencyRecovery:
    """Verifies the authorized operator emergency recovery procedure."""

    def test_recovery_fails_with_insufficient_justification(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_1", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Stall", source="api")

        # Justification too short (< 10 chars)
        with pytest.raises(ValueError) as exc_info:
            manager.operator_emergency_recovery(
                operator_justification="too short",
                confirmed_terminated_operations=["op_1"],
            )
        assert "at least 10 characters" in str(exc_info.value)
        assert manager.is_emergency_stop_active() is True

    def test_recovery_fails_when_unaccounted_operations_exist(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_mouse", execution_id="exec_1")
        manager.register_active_operation("op_keyboard", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Dual stall", source="api")

        # Only op_mouse is confirmed; op_keyboard is missing
        with pytest.raises(UnsafeResetError) as exc_info:
            manager.operator_emergency_recovery(
                operator_justification="Verified mouse process was terminated.",
                confirmed_terminated_operations=["op_mouse"],
            )
        assert "not accounted for in confirmed_terminated_operations" in str(exc_info.value)
        assert "op_keyboard" in str(exc_info.value)
        assert manager.is_emergency_stop_active() is True

    def test_recovery_fails_if_process_pid_is_still_alive(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_calc", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Halt", source="api")

        # Use current process PID which is guaranteed to be alive
        current_pid = os.getpid()

        with pytest.raises(UnsafeResetError) as exc_info:
            manager.operator_emergency_recovery(
                operator_justification="Operator inspected system and checked running processes.",
                confirmed_terminated_operations=["op_calc"],
                verification_evidence={"pids": [current_pid]},
            )
        assert f"Process with PID {current_pid} is still actively running" in str(exc_info.value)
        assert manager.is_emergency_stop_active() is True

    def test_recovery_succeeds_with_valid_evidence_and_justification(self, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_browser", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Halt", source="api")

        # High fictitious PID that does not exist
        dead_pid = 999999

        status = manager.operator_emergency_recovery(
            operator_justification="Operator confirmed browser subprocess killed and handles closed.",
            confirmed_terminated_operations=["op_browser"],
            verification_evidence={"pids": [dead_pid]},
        )

        assert status["is_active"] is False
        assert not manager.is_emergency_stop_active()
        assert len(manager.list_active_operations()) == 0


class TestOperatorRecoveryApiEndpoint:
    """Verifies the HTTP endpoints /agent/emergency-stop/reset and /recover."""

    def test_api_reset_rejected_with_active_ops(self, client: TestClient, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_active", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Test", source="api")

        res = client.post("/agent/emergency-stop/reset", headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 409
        assert "active operations have not reached a confirmed safe terminal state" in res.json()["detail"]

    def test_api_recovery_endpoint_succeeds(self, client: TestClient, clean_manager: EmergencyStopManager):
        manager = clean_manager
        manager.register_active_operation("op_stuck", execution_id="exec_1")
        manager.trigger_emergency_stop(reason="Test", source="api")

        payload = {
            "operator_justification": "Operator verified all processes terminated cleanly.",
            "confirmed_terminated_operations": ["op_stuck"],
            "verification_evidence": {"pids": [999998]},
        }
        res = client.post("/agent/emergency-stop/recover", json=payload, headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["is_active"] is False
        assert not manager.is_emergency_stop_active()

