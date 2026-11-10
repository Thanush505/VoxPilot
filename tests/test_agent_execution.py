"""
Integration and unit tests for the VoxPilot LangGraph Agent workflow,
plan validation, tool execution loops, confirmation trust boundaries, and execution resumption.
"""

from unittest.mock import MagicMock, patch
import pytest

from backend.agent.graph import VoxPilotAgent
from backend.agent.state import VoxPilotState, ExecutionResult
from backend.agent.tool_registry import ToolRegistry, ToolDefinition
from backend.agent.tool_executor import ToolExecutor
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    ConfirmationStatus,
    MissingUserApprovalError,
)
from pydantic import BaseModel, Field


# ------------------------------------------------------------------------------
# Fixtures and Helpers
# ------------------------------------------------------------------------------

@pytest.fixture
def confirmation_mgr() -> ConfirmationManager:
    return ConfirmationManager()


@pytest.fixture
def custom_registry() -> ToolRegistry:
    reg = ToolRegistry(register_defaults=True)
    return reg


def make_mock_agent(
    intent: str = "system_information",
    steps: list = None,
    confirmation_mgr: ConfirmationManager = None,
    registry: ToolRegistry = None,
) -> VoxPilotAgent:
    cm = confirmation_mgr or ConfirmationManager()
    reg = registry or ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    mock_intent_rec = MagicMock()
    mock_intent_rec.recognize_intent.return_value = {
        "intent": intent,
        "confidence": 0.95,
        "reason": "Test intent classification",
    }

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": steps or [],
        "response": "Planning completed.",
    }

    return VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_rec,
        planner=mock_planner,
    )


# ==============================================================================
# Plan Validation Tests
# ==============================================================================

def test_plan_validation_empty_plan():
    """Empty plan for non-conversational intent must fail validation."""
    agent = make_mock_agent(intent="open_application", steps=[])
    state = agent.process_request("open something")

    assert state["execution_status"] == "validation_failed"
    assert "no actionable steps" in state["error"].lower()
    assert len(state["completed_steps"]) == 0


def test_plan_validation_general_conversation_empty_plan():
    """Conversational intent with no tool steps completes successfully."""
    agent = make_mock_agent(intent="general_conversation", steps=[])
    state = agent.process_request("hello there")

    assert state["execution_status"] == "completed"
    assert "Hello!" in state["response"] or state["response"] != ""
    assert len(state["completed_steps"]) == 0


def test_plan_validation_exceeds_max_steps():
    """Plan exceeding 10 steps must be halted by validation guard."""
    excessive_steps = [
        {"action": "get_system_info", "description": f"Step {i}", "parameters": {}}
        for i in range(11)
    ]
    agent = make_mock_agent(intent="system_information", steps=excessive_steps)
    state = agent.process_request("check system 11 times")

    assert state["execution_status"] == "validation_failed"
    assert "exceeds maximum allowed safety limit of 10" in state["error"]


def test_plan_validation_unknown_action():
    """Plan containing unapproved action must fail validation."""
    invalid_steps = [
        {"action": "format_c_drive", "description": "Destructive format", "parameters": {}}
    ]
    agent = make_mock_agent(intent="system_information", steps=invalid_steps)
    state = agent.process_request("format drive")

    assert state["execution_status"] == "validation_failed"
    assert "unknown or unapproved action 'format_c_drive'" in state["error"]


def test_plan_validation_invalid_parameters():
    """Plan with invalid parameter types or missing fields must fail validation."""
    invalid_param_steps = [
        {"action": "launch_application", "description": "Missing application_name", "parameters": {}}
    ]
    agent = make_mock_agent(intent="open_application", steps=invalid_param_steps)
    state = agent.process_request("open app")

    assert state["execution_status"] == "validation_failed"
    assert "parameters for 'launch_application' failed validation" in state["response"].lower() or "missing" in state["error"].lower()


def test_plan_validation_unknown_parameters():
    """Plan with unknown/injected parameters must fail validation."""
    injected_param_steps = [
        {
            "action": "launch_application",
            "description": "Injected param",
            "parameters": {"application_name": "notepad", "injected_param": True},
        }
    ]
    agent = make_mock_agent(intent="open_application", steps=injected_param_steps)
    state = agent.process_request("open notepad injected")

    assert state["execution_status"] == "validation_failed"
    assert "unknown/unsupported parameter" in state["error"].lower()


def test_plan_validation_malformed_step_structure():
    """Plan containing non-dictionary step elements must fail validation."""
    malformed_steps = ["just_a_string_not_a_dict"]
    agent = make_mock_agent(intent="open_application", steps=malformed_steps)
    state = agent.process_request("malformed step")

    assert state["execution_status"] == "validation_failed"
    assert "malformed" in state["error"].lower()


# ==============================================================================
# Tool Execution Tests
# ==============================================================================

def test_successful_single_tool_execution():
    """Agent successfully executes a safe single-step tool (get_system_info)."""
    steps = [
        {
            "action": "get_system_info",
            "description": "Retrieve system status",
            "parameters": {},
        }
    ]
    agent = make_mock_agent(intent="system_information", steps=steps)
    state = agent.process_request("get system metrics")

    assert state["execution_status"] == "completed"
    assert len(state["completed_steps"]) == 1
    assert len(state["execution_results"]) == 1
    res = state["execution_results"][0]
    assert res["success"] is True
    assert res["action"] == "get_system_info"
    assert "CPU" in res["message"] or "System info" in res["message"]
    assert state["response"] != ""


def test_successful_multi_step_execution_loop():
    """Agent loops sequentially across multiple valid steps until finished."""
    steps = [
        {
            "action": "get_system_info",
            "description": "Check system",
            "parameters": {},
        },
        {
            "action": "respond",
            "description": "Finished checking",
            "parameters": {},
        },
    ]
    agent = make_mock_agent(intent="system_information", steps=steps)
    state = agent.process_request("check system and report")

    assert state["execution_status"] == "completed"
    assert len(state["completed_steps"]) == 2
    assert len(state["execution_results"]) == 2
    assert state["completed_steps"][0]["action"] == "get_system_info"
    assert state["completed_steps"][1]["action"] == "respond"


def test_execution_failure_stops_workflow():
    """Execution error stops subsequent steps and reports truthful failure."""
    reg = ToolRegistry(register_defaults=True)

    class FailingParam(BaseModel):
        pass

    def failing_handler(data):
        return {"success": False, "error": "Hardware sensor failure"}

    reg.register(
        ToolDefinition(
            name="failing_sensor_tool",
            category="custom",
            description="Tool that fails",
            input_schema=FailingParam,
            func=failing_handler,
        )
    )

    steps = [
        {"action": "failing_sensor_tool", "description": "Fails here", "parameters": {}},
        {"action": "get_system_info", "description": "Should never execute", "parameters": {}},
    ]
    agent = make_mock_agent(intent="system_information", steps=steps, registry=reg)
    state = agent.process_request("run sensor")

    assert state["execution_status"] == "failed"
    assert len(state["completed_steps"]) == 0
    assert len(state["failed_steps"]) == 1
    assert "failing_sensor_tool" in state["failed_steps"][0]["action"]
    assert "halted at step 1" in state["response"]
    assert len(state["execution_results"]) == 1
    assert state["execution_results"][0]["success"] is False


# ==============================================================================
# Human Confirmation Trust Boundary Tests
# ==============================================================================

def test_destructive_tool_pauses_for_confirmation():
    """Consequential action (delete_file) pauses execution and creates confirmation token."""
    steps = [
        {
            "action": "delete_file",
            "description": "Delete temporary test file",
            "parameters": {"file_path": "temp.txt"},
        }
    ]
    agent = make_mock_agent(intent="file_management", steps=steps)
    state = agent.process_request("delete temp file")

    assert state["execution_status"] == "confirmation_required"
    assert state["requires_confirmation"] is True
    assert state["pending_confirmation"] is not None

    pending = state["pending_confirmation"]
    assert pending["action"] == "delete_file"
    assert pending["token"] is not None
    assert len(pending["token"]) > 20
    assert "requires your confirmation" in state["response"]
    # Ensure tool was NOT executed yet
    assert len(state["completed_steps"]) == 0


def test_llm_confirmed_flag_cannot_bypass_confirmation_boundary():
    """Adversarial attempt: LLM providing confirmed=True does NOT bypass confirmation."""
    steps = [
        {
            "action": "delete_file",
            "description": "Delete file with LLM self-approval",
            "parameters": {"file_path": "important.txt", "confirmed": True},
        }
    ]
    agent = make_mock_agent(intent="file_management", steps=steps)
    state = agent.process_request("delete file now")

    # Confirmation must STILL be required
    assert state["execution_status"] == "confirmation_required"
    assert state["pending_confirmation"] is not None


def test_resume_execution_after_approval(tmp_path):
    """Approving confirmation token allows resumed execution to complete."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)

    # Register a safe mock destructive tool
    executed = []

    class MockDestructiveInput(BaseModel):
        target: str = Field(...)
        confirmation_token: str = Field(default="")

    def mock_destructive_tool(data: MockDestructiveInput):
        executed.append(data.target)
        return {"success": True, "message": f"Deleted {data.target}"}

    reg.register(
        ToolDefinition(
            name="mock_delete",
            category="filesystem",
            description="Mock destructive action",
            input_schema=MockDestructiveInput,
            func=mock_destructive_tool,
            inherent_confirmation=True,
            confirmation_action_type="mock_delete",
        )
    )

    steps = [
        {
            "action": "mock_delete",
            "description": "Delete sample item",
            "parameters": {"target": "item_123"},
        }
    ]
    agent = make_mock_agent(
        intent="file_management",
        steps=steps,
        confirmation_mgr=cm,
        registry=reg,
    )

    # 1. First execution pauses
    state = agent.process_request("delete item")
    assert state["execution_status"] == "confirmation_required"
    token = state["pending_confirmation"]["token"]
    assert len(executed) == 0

    # 2. Resuming before user approval results in failure and does NOT execute tool
    unapproved_state = agent.resume_execution(state, confirmation_token=token)
    assert unapproved_state["execution_status"] == "failed"
    assert "pending user approval" in unapproved_state["error"].lower()
    assert len(executed) == 0

    # 3. User grants approval
    cm.approve_confirmation(token)

    # 4. Resume execution
    final_state = agent.resume_execution(state, confirmation_token=token)
    assert final_state["execution_status"] == "completed"
    assert len(executed) == 1
    assert executed[0] == "item_123"
    assert len(final_state["completed_steps"]) == 1
    assert "Deleted item_123" in final_state["response"]


def test_resume_execution_invalid_state_guard():
    """Attempting resume_execution on an unpaused state raises ValueError."""
    agent = make_mock_agent(intent="system_information", steps=[])
    dummy_state: VoxPilotState = {
        "user_input": "test",
        "execution_id": "exec_1",
        "execution_status": "completed",
        "current_step": 0,
        "plan": [],
        "execution_results": [],
        "completed_steps": [],
        "failed_steps": [],
        "error": "",
        "requires_confirmation": False,
        "pending_confirmation": None,
    }

    with pytest.raises(ValueError) as exc:
        agent.resume_execution(dummy_state, confirmation_token="tok123")
    assert "expected 'confirmation_required'" in str(exc.value)


def test_empty_user_input_guard():
    """Empty or whitespace user_input raises ValueError immediately."""
    agent = make_mock_agent()
    with pytest.raises(ValueError):
        agent.process_request("")
    with pytest.raises(ValueError):
        agent.process_request("   ")
