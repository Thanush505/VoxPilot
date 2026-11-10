"""
Unit and integration tests for LangGraph Agent and Voice Pipeline Emergency Stop Cancellation.
Verifies pre-execution rejection, mid-workflow step halts, preservation of completed steps,
prevention of dynamic re-planning during emergency stop, and voice pipeline safeguards.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from backend.agent.graph import VoxPilotAgent
from backend.agent.state import VoxPilotState, ExecutionResult
from backend.agent.tool_registry import ToolRegistry, ToolDefinition
from backend.agent.tool_executor import ToolExecutor
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
)
from backend.automation.confirmation_manager import ConfirmationManager
from backend.database.connection import init_db_sync
from backend.voice.voice_pipeline import VoicePipeline
from pydantic import BaseModel, Field


class DummyParam(BaseModel):
    value: str = Field("default", description="test value")


@pytest.fixture(autouse=True)
def setup_test_env():
    """Initialize database and fresh emergency stop coordinator for each test."""
    init_db_sync()
    manager = EmergencyStopManager()
    set_shared_emergency_stop_manager(manager)
    yield manager
    if manager.is_emergency_stop_active():
        manager.reset_emergency_stop(force=True)


def build_cancellation_test_agent(
    intent: str = "desktop_control",
    steps: list = None,
    manager: EmergencyStopManager = None,
) -> VoxPilotAgent:
    reg = ToolRegistry(register_defaults=False)
    cm = ConfirmationManager()
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    # Register dummy tools
    def dummy_tool_1(params: DummyParam):
        return {"success": True, "message": "Step 1 succeeded", "data": {"key": "val"}}

    def dummy_tool_2(params: DummyParam):
        return {"success": True, "message": "Step 2 succeeded", "data": {}}

    reg.register(
        ToolDefinition(
            name="tool_one",
            category="custom",
            description="First step tool",
            input_schema=DummyParam,
            func=dummy_tool_1,
        )
    )
    reg.register(
        ToolDefinition(
            name="tool_two",
            category="custom",
            description="Second step tool",
            input_schema=DummyParam,
            func=dummy_tool_2,
        )
    )

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": intent,
        "confidence": 0.99,
        "reason": "Test",
    }

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": steps or [
            {"action": "tool_one", "parameters": {"value": "first"}, "description": "Run first"},
            {"action": "tool_two", "parameters": {"value": "second"}, "description": "Run second"},
        ],
        "response": "Executing steps",
    }

    return VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
    )


class TestAgentCancellation:
    """Tests agent workflow cancellation and emergency stop safeguards."""

    def test_process_request_rejected_immediately_when_emergency_stop_active(
        self, setup_test_env: EmergencyStopManager
    ):
        manager = setup_test_env
        manager.trigger_emergency_stop(reason="Active emergency stop latch", source="hotkey")

        agent = build_cancellation_test_agent()
        state = agent.process_request("open browser and search")

        assert state["execution_status"] == "cancelled"
        assert "emergency stop is active" in state["response"].lower()
        assert len(state["plan"]) == 0
        assert len(state["execution_results"]) == 0

    def test_resume_execution_rejected_immediately_when_emergency_stop_active(
        self, setup_test_env: EmergencyStopManager
    ):
        manager = setup_test_env
        agent = build_cancellation_test_agent()

        paused_state: VoxPilotState = {
            "execution_id": "exec_paused_123",
            "conversation_id": "conv_123",
            "user_input": "delete database",
            "execution_status": "confirmation_required",
            "current_step": 0,
            "plan": [{"action": "tool_one", "parameters": {}, "description": "Delete"}],
            "execution_results": [],
            "completed_steps": [],
            "failed_steps": [],
            "error": "",
            "requires_confirmation": True,
            "pending_confirmation": {"token": "valid_tok_123", "step_index": 0},
            "replan_count": 0,
            "max_replans": 2,
            "needs_replan": False,
            "replan_reason": None,
            "replan_history": [],
        }

        # Trigger emergency stop while paused
        manager.trigger_emergency_stop(reason="Operator cancelled all executions", source="api")

        resumed = agent.resume_execution(paused_state, confirmation_token="valid_tok_123")
        assert resumed["execution_status"] == "cancelled"
        assert "emergency stop is active" in resumed["error"].lower()

    def test_mid_workflow_step_halts_and_preserves_completed_steps(
        self, setup_test_env: EmergencyStopManager
    ):
        manager = setup_test_env
        agent = build_cancellation_test_agent()

        # Wrap step 1 to trigger emergency stop after its successful execution
        orig_execute = agent.tool_executor.execute_step

        def intercept_execute(step, step_index=0, override_confirmation_token=None):
            res = orig_execute(step, step_index, override_confirmation_token)
            if step_index == 0:
                # Trigger emergency stop immediately after step 0 finishes
                manager.trigger_emergency_stop(
                    reason="Global hotkey pressed during workflow execution", source="hotkey"
                )
            return res

        agent.tool_executor.execute_step = intercept_execute

        state = agent.process_request("run two tasks")

        assert state["execution_status"] == "cancelled"
        # Completed steps contains step 0
        assert len(state["completed_steps"]) == 1
        assert state["completed_steps"][0]["action"] == "tool_one"
        # Step 1 was not dispatched or completed
        assert len(state["completed_steps"]) < len(state["plan"])
        assert "Execution halted" in state["response"] or "cancelled" in state["response"].lower()

    def test_emergency_stop_prevents_dynamic_replanning(
        self, setup_test_env: EmergencyStopManager
    ):
        manager = setup_test_env
        agent = build_cancellation_test_agent()

        # Step 0 triggers stop
        def replan_triggering_execute(step, step_index=0, override_confirmation_token=None):
            manager.trigger_emergency_stop(reason="Emergency abort", source="api")
            return ExecutionResult(
                step_index=step_index,
                action=step.get("action", ""),
                success=True,
                status="completed",
                message="Done",
                data={"matches": ["file1.txt", "file2.txt"]},
            )

        agent.tool_executor.execute_step = replan_triggering_execute
        agent.planner.replan_from_results = MagicMock(return_value=([], "Replanned"))

        state = agent.process_request("find file and open")

        # Dynamic replanning must be blocked
        assert state["execution_status"] == "cancelled"
        agent.planner.replan_from_results.assert_not_called()

    def test_voice_pipeline_rejects_command_when_emergency_stop_active(
        self, setup_test_env: EmergencyStopManager
    ):
        manager = setup_test_env
        manager.trigger_emergency_stop(reason="Voice stop safety check", source="hotkey")

        mock_agent = MagicMock()
        pipeline = VoicePipeline(
            microphone_device=None,
            whisper_model="base",
            ollama_model="qwen3:4b",
            enable_wake_word=False,
            agent=mock_agent,
        )

        response = pipeline.generate_response("Open Google Chrome")

        assert "emergency stop is currently active" in response.lower()
        assert "please reset emergency stop" in response.lower()
        mock_agent.process_request.assert_not_called()
