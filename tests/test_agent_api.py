"""
Integration tests for VoxPilot FastAPI agent endpoints and confirmation management API.
"""

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.agent import set_agent, clear_executions, save_execution
from backend.agent.graph import VoxPilotAgent
from backend.agent.tool_registry import ToolRegistry, ToolDefinition
from backend.agent.tool_executor import ToolExecutor
from backend.automation.confirmation_manager import ConfirmationManager
from backend.database.connection import init_db_sync
from pydantic import BaseModel, Field


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Ensure clean execution store and shared agent before and after each test."""
    init_db_sync()
    clear_executions()
    yield
    clear_executions()
    set_agent(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def create_test_agent(
    intent: str,
    steps: list,
    confirmation_mgr: ConfirmationManager = None,
    registry: ToolRegistry = None,
) -> VoxPilotAgent:
    cm = confirmation_mgr or ConfirmationManager()
    reg = registry or ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    mock_intent_rec = MagicMock()
    mock_intent_rec.recognize_intent.return_value = {
        "intent": intent,
        "confidence": 0.98,
        "reason": "API test intent",
    }

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": steps,
        "response": "API test plan generated.",
    }

    return VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_rec,
        planner=mock_planner,
    )


# ==============================================================================
# Health and Service Info Endpoints
# ==============================================================================

def test_root_endpoint(client: TestClient):
    """Verify service root returns metadata and online status."""
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["version"] in ("0.4.0", "0.5.0")


def test_health_check_endpoint(client: TestClient):
    """Verify health check returns healthy status."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


# ==============================================================================
# Execute Endpoint Tests
# ==============================================================================

def test_execute_empty_input_rejected(client: TestClient):
    """Empty or whitespace input must return 400 Bad Request."""
    res = client.post("/agent/execute", json={"user_input": ""})
    assert res.status_code == 422 or res.status_code == 400

    res2 = client.post("/agent/execute", json={"user_input": "   "})
    assert res2.status_code == 400
    assert "empty or whitespace" in res2.json()["detail"].lower()


def test_execute_successful_request(client: TestClient):
    """Execute a simple safe request and verify execution store."""
    agent = create_test_agent(
        intent="system_information",
        steps=[{"action": "get_system_info", "description": "Get metrics", "parameters": {}}],
    )
    set_agent(agent)

    res = client.post("/agent/execute", json={"user_input": "Show system status"})
    assert res.status_code == 200
    data = res.json()
    assert data["execution_status"] == "completed"
    assert data["user_input"] == "Show system status"
    assert data["execution_id"].startswith("exec_")
    assert len(data["completed_steps"]) == 1

    # Verify status query by execution_id
    status_res = client.get(f"/agent/status/{data['execution_id']}")
    assert status_res.status_code == 200
    assert status_res.json()["execution_id"] == data["execution_id"]


def test_execute_custom_execution_id(client: TestClient):
    """Custom execution_id in request is honored and persisted."""
    agent = create_test_agent(intent="general_conversation", steps=[])
    set_agent(agent)

    res = client.post(
        "/agent/execute",
        json={"user_input": "Hello", "execution_id": "custom_uuid_42"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["execution_id"] == "custom_uuid_42"


def test_get_status_not_found(client: TestClient):
    """Status query for non-existent execution returns 404."""
    res = client.get("/agent/status/non_existent_id")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


# ==============================================================================
# Confirmation Workflow Endpoints (Inspect, Approve, Reject)
# ==============================================================================

def test_confirmation_lifecycle_approve(client: TestClient):
    """Full lifecycle: consequential action pauses -> inspect confirmation -> approve -> completed."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)

    class ConsequentialInput(BaseModel):
        target: str = Field(...)
        confirmation_token: str = Field(default="")

    reg.register(
        ToolDefinition(
            name="safe_mock_delete",
            category="filesystem",
            description="Mock destructive action",
            input_schema=ConsequentialInput,
            func=lambda data: {"success": True, "message": f"Successfully deleted {data.target}"},
            inherent_confirmation=True,
            confirmation_action_type="safe_mock_delete",
        )
    )

    agent = create_test_agent(
        intent="file_management",
        steps=[
            {
                "action": "safe_mock_delete",
                "description": "Delete sample file",
                "parameters": {"target": "test.txt"},
            }
        ],
        confirmation_mgr=cm,
        registry=reg,
    )
    set_agent(agent)

    # 1. Trigger execution -> pauses awaiting confirmation
    res = client.post("/agent/execute", json={"user_input": "Delete test.txt"})
    assert res.status_code == 200
    exec_data = res.json()
    assert exec_data["execution_status"] == "confirmation_required"
    exec_id = exec_data["execution_id"]
    token = exec_data["pending_confirmation"]["token"]
    assert token

    # 2. Inspect pending confirmation
    conf_res = client.get(f"/agent/confirmations/{exec_id}")
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["token"] == token
    assert conf_data["action"] == "safe_mock_delete"

    # 3. Approve confirmation
    approve_res = client.post(
        f"/agent/confirmations/{exec_id}/approve",
        json={"token": token},
    )
    assert approve_res.status_code == 200
    approved_data = approve_res.json()
    assert approved_data["execution_status"] == "completed"
    assert approved_data["pending_confirmation"] is None


def test_confirmation_lifecycle_reject(client: TestClient):
    """Consequential action pauses -> reject confirmation -> execution cancelled."""
    cm = ConfirmationManager()
    agent = create_test_agent(
        intent="file_management",
        steps=[
            {
                "action": "delete_file",
                "description": "Delete sample file",
                "parameters": {"file_path": "test.txt"},
            }
        ],
        confirmation_mgr=cm,
    )
    set_agent(agent)

    # 1. Trigger execution -> pauses
    res = client.post("/agent/execute", json={"user_input": "Delete test.txt"})
    exec_id = res.json()["execution_id"]
    token = res.json()["pending_confirmation"]["token"]

    # 2. Reject confirmation
    reject_res = client.post(
        f"/agent/confirmations/{exec_id}/reject",
        json={"token": token, "reason": "User changed mind"},
    )
    assert reject_res.status_code == 200
    reject_data = reject_res.json()
    assert reject_data["execution_status"] == "cancelled"
    assert "User changed mind" in reject_data["response"]
    assert reject_data["pending_confirmation"] is None


def test_confirmation_token_mismatch_rejected(client: TestClient):
    """Submitting wrong confirmation token returns 403 Forbidden."""
    cm = ConfirmationManager()
    agent = create_test_agent(
        intent="file_management",
        steps=[
            {
                "action": "delete_file",
                "description": "Delete sample file",
                "parameters": {"file_path": "test.txt"},
            }
        ],
        confirmation_mgr=cm,
    )
    set_agent(agent)

    res = client.post("/agent/execute", json={"user_input": "Delete test.txt"})
    exec_id = res.json()["execution_id"]

    approve_res = client.post(
        f"/agent/confirmations/{exec_id}/approve",
        json={"token": "wrong_attacker_token_12345"},
    )
    assert approve_res.status_code == 403
    assert "token does not match" in approve_res.json()["detail"].lower()


def test_confirmation_approve_not_awaiting(client: TestClient):
    """Calling approve on a completed execution returns 400 Bad Request."""
    agent = create_test_agent(intent="general_conversation", steps=[])
    set_agent(agent)

    res = client.post("/agent/execute", json={"user_input": "Hi"})
    exec_id = res.json()["execution_id"]

    approve_res = client.post(
        f"/agent/confirmations/{exec_id}/approve",
        json={"token": "some_token"},
    )
    assert approve_res.status_code == 400
    assert "not awaiting confirmation" in approve_res.json()["detail"].lower()


# ==============================================================================
# Tool Listing Endpoint
# ==============================================================================

def test_list_tools_endpoint(client: TestClient):
    """Verify GET /agent/tools lists registered tools with metadata."""
    res = client.get("/agent/tools")
    assert res.status_code == 200
    tools = res.json()
    assert isinstance(tools, list)
    assert len(tools) > 10

    names = {t["name"] for t in tools}
    assert "launch_application" in names
    assert "delete_file" in names
    assert "get_system_info" in names

    delete_tool = next(t for t in tools if t["name"] == "delete_file")
    assert delete_tool["is_consequential"] is True

    sys_tool = next(t for t in tools if t["name"] == "get_system_info")
    assert sys_tool["is_consequential"] is False


# ==============================================================================
# Database Fallback and Conversation Management Tests
# ==============================================================================

def test_get_status_database_fallback(client: TestClient):
    """When execution is purged from memory, GET /agent/status/{id} falls back to SQLite database."""
    agent = create_test_agent(
        intent="system_information",
        steps=[{"action": "get_system_info", "description": "Check info", "parameters": {}}],
    )
    set_agent(agent)

    # 1. Execute request
    res = client.post("/agent/execute", json={"user_input": "show system info", "execution_id": "exec_db_fallback_test"})
    assert res.status_code == 200
    exec_id = res.json()["execution_id"]

    # 2. Evict execution from in-memory cache
    clear_executions()

    # 3. GET /agent/status should fallback to database and succeed
    status_res = client.get(f"/agent/status/{exec_id}")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["execution_id"] == exec_id
    assert data["execution_status"] == "completed"
    assert data["user_input"] == "show system info"


def test_conversation_management_endpoints(client: TestClient):
    """Verify listing conversations, retrieving conversation details, and fetching conversation executions."""
    agent = create_test_agent(
        intent="system_information",
        steps=[{"action": "get_system_info", "description": "Check metrics", "parameters": {}}],
    )
    set_agent(agent)

    conv_id = "conv_api_test_999"

    # 1. Execute within custom conversation
    res = client.post(
        "/agent/execute",
        json={"user_input": "get metrics", "conversation_id": conv_id},
    )
    assert res.status_code == 200
    exec_id = res.json()["execution_id"]

    # 2. List conversations
    list_res = client.get("/agent/conversations")
    assert list_res.status_code == 200
    conv_list = list_res.json()
    assert any(c["conversation_id"] == conv_id for c in conv_list)

    # 3. Get conversation details
    detail_res = client.get(f"/agent/conversations/{conv_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["conversation_id"] == conv_id
    assert detail["execution_count"] >= 1

    # 4. List executions for conversation
    execs_res = client.get(f"/agent/conversations/{conv_id}/executions")
    assert execs_res.status_code == 200
    execs = execs_res.json()
    assert any(e["execution_id"] == exec_id for e in execs)

    # 5. Non-existent conversation returns 404
    not_found_res = client.get("/agent/conversations/nonexistent_conv_000")
    assert not_found_res.status_code == 404
