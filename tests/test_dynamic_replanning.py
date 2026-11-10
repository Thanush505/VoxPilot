"""
Unit and integration tests for VoxPilot dynamic re-planning subsystem.
Tests adaptive plan updates from discovery tools (search_files, find_windows, launch_application),
HWND runtime binding, intermediate failure aborts, schema validation of revised plans,
MAX_REPLANS limit enforcement, and preservation of completed steps.
"""

import pytest
from unittest.mock import MagicMock
from pydantic import BaseModel, Field

from backend.agent.graph import VoxPilotAgent
from backend.agent.planner import AgentPlanner
from backend.agent.tool_registry import ToolRegistry, ToolDefinition
from backend.agent.tool_executor import ToolExecutor
from backend.automation.confirmation_manager import ConfirmationManager


@pytest.fixture
def base_components():
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)
    return cm, reg, executor


# ==============================================================================
# Search Result Dynamic Re-Planning Tests
# ==============================================================================

def test_single_search_result_binds_path_to_subsequent_step(base_components):
    """When search_files returns a single match, replanner binds concrete path to get_file_metadata."""
    cm, reg, executor = base_components

    # Planner originally generates search_files, then get_file_metadata with placeholder path
    planner = AgentPlanner()
    original_steps = [
        {"action": "search_files", "description": "Search for notes", "parameters": {"directory_path": "C:/docs", "query": "notes"}},
        {"action": "get_file_metadata", "description": "Read notes file", "parameters": {"file_path": "placeholder.txt"}},
    ]
    completed_steps = [original_steps[0]]
    completed_results = [{
        "action": "search_files",
        "success": True,
        "data": {"matches": ["C:/docs/notes_2026.txt"]},
        "message": "Found 1 matching file.",
    }]

    revised_steps, reason = planner.replan_from_results(
        original_input="find notes and read it",
        completed_steps=completed_steps,
        completed_results=completed_results,
        remaining_plan=[original_steps[1]],
    )

    assert len(revised_steps) == 1
    assert revised_steps[0]["action"] == "get_file_metadata"
    assert revised_steps[0]["parameters"]["file_path"] == "C:/docs/notes_2026.txt"
    assert "Discovered single matching file" in reason


def test_zero_search_results_aborts_subsequent_steps(base_components):
    """When search_files finds 0 matches, remaining file actions are replaced with safe notification."""
    planner = AgentPlanner()
    original_steps = [
        {"action": "search_files", "description": "Search file", "parameters": {"directory_path": "C:/docs", "query": "missing"}},
        {"action": "get_file_metadata", "description": "Read file", "parameters": {"file_path": "missing.txt"}},
    ]
    completed_steps = [original_steps[0]]
    completed_results = [{
        "action": "search_files",
        "success": True,
        "data": {"matches": []},
        "message": "Found 0 files.",
    }]

    revised_steps, reason = planner.replan_from_results(
        original_input="find missing and read it",
        completed_steps=completed_steps,
        completed_results=completed_results,
        remaining_plan=[original_steps[1]],
    )

    assert len(revised_steps) == 1
    assert revised_steps[0]["action"] == "respond"
    assert "no matching files were found" in revised_steps[0]["description"].lower()


def test_multiple_search_results_requests_clarification(base_components):
    """When multiple matches are found, replanner halts and asks user for clarification."""
    planner = AgentPlanner()
    original_steps = [
        {"action": "search_files", "description": "Search file", "parameters": {"directory_path": "C:/docs", "query": "report"}},
        {"action": "get_file_metadata", "description": "Read file", "parameters": {"file_path": "report.pdf"}},
    ]
    completed_steps = [original_steps[0]]
    completed_results = [{
        "action": "search_files",
        "success": True,
        "data": {"matches": ["C:/docs/report_jan.pdf", "C:/docs/report_feb.pdf"]},
        "message": "Found 2 files.",
    }]

    revised_steps, reason = planner.replan_from_results(
        original_input="open report",
        completed_steps=completed_steps,
        completed_results=completed_results,
        remaining_plan=[original_steps[1]],
    )

    assert len(revised_steps) == 1
    assert revised_steps[0]["action"] == "ask_clarification"
    assert "multiple" in reason.lower()


# ==============================================================================
# Window Discovery HWND Binding Tests
# ==============================================================================

def test_application_launch_binds_hwnd_to_subsequent_interaction(base_components):
    """When launch_application returns a window HWND, subsequent keyboard steps receive the HWND."""
    planner = AgentPlanner()
    original_steps = [
        {"action": "launch_application", "description": "Open Notepad", "parameters": {"application_name": "notepad"}},
        {"action": "type_text", "description": "Type text", "parameters": {"text": "Hello world"}},
    ]
    completed_steps = [original_steps[0]]
    completed_results = [{
        "action": "launch_application",
        "success": True,
        "data": {"hwnd": 77777, "title": "Untitled - Notepad"},
        "message": "Application launched.",
    }]

    revised_steps, reason = planner.replan_from_results(
        original_input="open notepad and type hello world",
        completed_steps=completed_steps,
        completed_results=completed_results,
        remaining_plan=[original_steps[1]],
    )

    assert len(revised_steps) == 1
    assert revised_steps[0]["action"] == "type_text"
    assert revised_steps[0]["parameters"]["hwnd"] == 77777
    assert "Bound active window HWND 77777" in reason or "77777" in reason


# ==============================================================================
# Intermediate Step Failure Abort Tests
# ==============================================================================

def test_intermediate_failure_stops_dependent_consequential_actions(base_components):
    """Failure at step 1 immediately stops execution and never runs step 2."""
    cm, reg, executor = base_components
    executed_tools = []

    class MockSafeParam(BaseModel):
        pass

    def step1_fail(data):
        executed_tools.append("step1")
        return {"success": False, "error": "Disk read failure"}

    def step2_consequential(data):
        executed_tools.append("step2")
        return {"success": True, "message": "Deleted"}

    reg.register(
        ToolDefinition(name="fail_step_1", category="custom", description="Fails", input_schema=MockSafeParam, func=step1_fail)
    )
    reg.register(
        ToolDefinition(name="consequential_step_2", category="custom", description="Should not run", input_schema=MockSafeParam, func=step2_consequential)
    )

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "Test"}

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {"action": "fail_step_1", "description": "Step 1", "parameters": {}},
            {"action": "consequential_step_2", "description": "Step 2", "parameters": {}},
        ],
        "response": "Executing steps.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
    )

    state = agent.process_request("run failure sequence")
    assert state["execution_status"] == "failed"
    assert executed_tools == ["step1"]
    assert "consequential_step_2" not in [s.get("action") for s in state.get("completed_steps", [])]


# ==============================================================================
# Schema Validation of Revised Plans
# ==============================================================================

def test_invalid_revised_plan_rejected_by_validation_node(base_components):
    """If a dynamic replan introduces an unregistered action, validation node catches and fails safely."""
    cm, reg, executor = base_components

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "Test"}

    # Mock planner whose replan introduces an invalid/unregistered tool name
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {"action": "get_system_info", "description": "Step 1", "parameters": {}},
            {"action": "get_system_info", "description": "Step 2", "parameters": {}},
        ],
        "response": "Two steps.",
    }
    # Replanner returns illegal action
    mock_planner.replan_from_results.return_value = (
        [{"action": "totally_illegal_nonexistent_action", "description": "Bad action", "parameters": {}}],
        "Testing invalid replan schema rejection.",
    )

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
    )

    # Force should_replan to trigger
    agent._should_replan = lambda state, action, result: True

    state = agent.process_request("run replan validation test")
    assert state["execution_status"] in ("validation_failed", "failed")
    err_msg = state.get("error", "").lower()
    assert any(term in err_msg for term in ("validation", "not registered", "unknown action", "unapproved"))


# ==============================================================================
# Hard Bounding: MAX_REPLANS Limit Enforcement
# ==============================================================================

def test_max_replans_bound_enforced(base_components):
    """Dynamic re-planning stops attempting replans after reaching max_replans=2."""
    cm, reg, executor = base_components

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
    )

    # State with replan_count at maximum
    state = {
        "replan_count": 2,
        "max_replans": 2,
        "user_input": "open notepad and type text",
        "current_step": 0,
        "plan": [{"action": "launch_application", "parameters": {"app_name": "notepad"}}],
    }

    # _should_replan should return False when limit reached
    should_replan = agent._should_replan(
        state=state,
        action="launch_application",
        result={"action": "launch_application", "success": True, "data": {"hwnd": 123}},
    )
    assert should_replan is False


# ==============================================================================
# Preservation of Completed Steps
# ==============================================================================

def test_completed_steps_preserved_and_never_reexecuted(base_components):
    """When replanning, previously completed steps remain in completed_steps list and are not re-run."""
    cm, reg, executor = base_components
    step_call_counts = {"step1": 0, "step2": 0}

    class Param(BaseModel):
        pass

    def run_step1(data):
        step_call_counts["step1"] += 1
        return {"success": True, "message": "Step 1 done"}

    def run_step2(data):
        step_call_counts["step2"] += 1
        return {"success": True, "message": "Step 2 done"}

    reg.register(ToolDefinition(name="step_one", category="custom", description="One", input_schema=Param, func=run_step1))
    reg.register(ToolDefinition(name="step_two", category="custom", description="Two", input_schema=Param, func=run_step2))

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "system_information", "confidence": 0.99, "reason": "Test"}

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {"action": "step_one", "description": "First step", "parameters": {}},
            {"action": "step_two", "description": "Second step", "parameters": {}},
        ],
        "response": "Executing steps.",
    }

    # Trigger 1 replan after step 1
    replan_invoked = [False]

    def mock_should_replan(state, action, result):
        if action == "step_one" and not replan_invoked[0]:
            replan_invoked[0] = True
            return True
        return False

    mock_planner.replan_from_results.return_value = (
        [{"action": "step_two", "description": "Second step (revised)", "parameters": {}}],
        "Replanned after step 1",
    )

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
    )
    agent._should_replan = mock_should_replan

    state = agent.process_request("run replanned steps")
    assert state["execution_status"] == "completed"
    # Step 1 was executed exactly ONCE
    assert step_call_counts["step1"] == 1
    # Step 2 was executed once
    assert step_call_counts["step2"] == 1
    # Both steps are in completed_steps
    assert len(state["completed_steps"]) == 2
    assert state["replan_count"] == 1
