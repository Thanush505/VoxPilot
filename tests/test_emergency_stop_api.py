"""
Integration tests for VoxPilot Emergency Stop REST API Endpoints.
Tests status retrieval, triggering, safe/forced resets, CSRF/Origin enforcement,
and verifies tool registry omission of reset tool.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
)
from backend.agent.tool_registry import get_shared_tool_registry
from backend.database.connection import init_db_sync


@pytest.fixture(autouse=True)
def setup_environment():
    """Ensure clean database and fresh EmergencyStopManager for each API test."""
    init_db_sync()
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


class TestEmergencyStopApi:
    """Test REST API endpoints for Emergency Stop."""

    def test_get_status_initial(self, client: TestClient):
        response = client.get("/agent/emergency-stop/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False
        assert data["active_operations_count"] == 0
        assert data["trigger_reason"] is None
        assert data["active_operations"] == []

    def test_trigger_emergency_stop_endpoint(self, client: TestClient):
        payload = {"reason": "Operator initiated emergency shutdown via UI"}
        response = client.post(
            "/agent/emergency-stop",
            json=payload,
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["is_active"] is True
        assert data["trigger_reason"] == "Operator initiated emergency shutdown via UI"
        assert data["trigger_source"] == "api"

        # Verify state in status endpoint
        status_res = client.get("/agent/emergency-stop/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["is_active"] is True
        assert status_data["trigger_reason"] == "Operator initiated emergency shutdown via UI"

    def test_safe_reset_endpoint_when_clear(self, client: TestClient):
        # Trigger first
        client.post("/agent/emergency-stop", json={"reason": "Test halt"})

        # Reset without force
        reset_res = client.post(
            "/agent/emergency-stop/reset",
            json={"force": False},
            headers={"Origin": "http://127.0.0.1:8000"},
        )
        assert reset_res.status_code == 200
        data = reset_res.json()
        assert data["success"] is True
        assert data["is_active"] is False

        # Status is now inactive
        status_res = client.get("/agent/emergency-stop/status")
        assert status_res.json()["is_active"] is False

    def test_unsafe_reset_rejected_with_http_409(self, client: TestClient, setup_environment: EmergencyStopManager):
        manager = setup_environment
        # Manually register an active in-flight operation
        manager.register_active_operation(
            operation_id="active_op_001",
            execution_id="exec_test",
            category="keyboard",
            description="Active typing loop",
        )
        # Trigger stop
        manager.trigger_emergency_stop(reason="Unsafe reset test", source="api")

        # Attempt reset with force=False
        reset_res = client.post(
            "/agent/emergency-stop/reset",
            json={"force": False},
            headers={"Origin": "http://localhost:8000"},
        )
        assert reset_res.status_code == 409
        err_detail = reset_res.json()["detail"]
        assert "Cannot reset emergency stop" in err_detail
        assert "active operations have not reached a confirmed safe terminal state" in err_detail

        # State remains active
        assert manager.is_emergency_stop_active() is True

    def test_force_reset_rejected_and_operator_recovery_succeeds(
        self, client: TestClient, setup_environment: EmergencyStopManager
    ):
        manager = setup_environment
        manager.register_active_operation(
            operation_id="stuck_op",
            execution_id="exec_test",
            category="browser",
            description="Hung page load",
        )
        manager.trigger_emergency_stop(reason="Force reset test", source="api")

        # Attempt reset with force=True -> strictly rejected
        reset_res = client.post(
            "/agent/emergency-stop/reset",
            json={"force": True},
            headers={"Origin": "http://localhost:3000"},
        )
        assert reset_res.status_code == 409
        assert "Unrestricted 'force=True' reset has been removed" in reset_res.json()["detail"]
        assert manager.is_emergency_stop_active() is True

        # Recovery endpoint with valid operator justification succeeds
        recover_res = client.post(
            "/agent/emergency-stop/recover",
            json={
                "operator_justification": "Operator inspected host and confirmed browser subprocess exited.",
                "confirmed_terminated_operations": ["stuck_op"],
            },
            headers={"Origin": "http://localhost:3000"},
        )
        assert recover_res.status_code == 200
        assert recover_res.json()["is_active"] is False
        assert not manager.is_emergency_stop_active()

    def test_csrf_origin_protection_on_emergency_stop_endpoints(self, client: TestClient):
        # Untrusted origin must be rejected with 403 Forbidden
        bad_headers = {"Origin": "http://malicious-website.com"}

        trigger_res = client.post(
            "/agent/emergency-stop",
            json={"reason": "CSRF attack attempt"},
            headers=bad_headers,
        )
        assert trigger_res.status_code == 403

        reset_res = client.post(
            "/agent/emergency-stop/reset",
            json={"force": True},
            headers=bad_headers,
        )
        assert reset_res.status_code == 403

    def test_reset_emergency_stop_strictly_omitted_from_llm_tool_registry(self):
        """Security requirement: emergency stop reset must NEVER be exposed as a tool to the LLM."""
        registry = get_shared_tool_registry()
        tool_names = [t.name.lower() for t in registry.list_tool_definitions()]
        assert "reset_emergency_stop" not in tool_names
        assert "emergency_stop_reset" not in tool_names
        assert not any("reset" in name and "stop" in name for name in tool_names)

