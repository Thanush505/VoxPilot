"""
Integration tests for LangGraph Agent Safety Checkpoints.
Verifies safety halting on emergency stop, budget exhaustion, rate limit,
step count limits, and confirmation rejection, ensuring completed steps are preserved.
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from backend.agent.graph import VoxPilotAgent
from backend.agent.state import VoxPilotState, ExecutionResult
from backend.agent.rate_limiter import (
    RateLimiter,
    RateLimiterConfig,
    set_shared_rate_limiter,
)
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    set_shared_emergency_stop_manager,
)


@pytest.fixture(autouse=True)
def setup_checkpoint_env():
    """Ensure clean emergency stop and rate limiter for each checkpoint test."""
    estop = EmergencyStopManager()
    set_shared_emergency_stop_manager(estop)

    limiter = RateLimiter(RateLimiterConfig(
        requests_per_minute=5,
        max_concurrent_executions=2,
        conversation_requests_per_minute=10,
        tool_calls_per_execution=3,
        tool_calls_per_minute=20,
        same_tool_per_execution=2,
        max_steps=4,
        max_duration_seconds=2.0,
    ))
    set_shared_rate_limiter(limiter)
    yield estop, limiter
    limiter.reset()


class TestLangGraphSafetyCheckpoints:
    """Verifies that the agent halts cleanly at each safety boundary."""

    def test_emergency_stop_halts_before_intent(self, setup_checkpoint_env):
        estop, _ = setup_checkpoint_env
        estop.trigger_emergency_stop(reason="User physically halted system", source="hotkey")

        agent = VoxPilotAgent()
        result = agent.process_request("open calculator", execution_id="exec_estop_1")

        assert result["execution_status"] == "cancelled"
        assert "emergency stop is active" in result["error"].lower()
        assert len(result["plan"]) == 0
        assert len(result["completed_steps"]) == 0

    def test_plan_step_count_budget_enforced(self, setup_checkpoint_env):
        _, limiter = setup_checkpoint_env
        # max_steps is 4 in our test config
        agent = VoxPilotAgent()

        # Mock intent recognizer to succeed without Ollama
        agent.intent_recognizer = MagicMock()
        agent.intent_recognizer.recognize_intent.return_value = {
            "intent": "system_control",
            "confidence": 0.95,
            "reason": "Test intent",
        }

        # Mock planner to return 5 steps
        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = {
            "steps": [
                {"action": "type_text", "parameters": {"text": f"step{i}"}}
                for i in range(5)
            ],
            "response": "Executing 5 steps",
        }
        agent.planner = mock_planner

        result = agent.process_request("run complex task", execution_id="exec_steps_1")

        assert result["execution_status"] == "validation_failed"
        assert "Plan step count (5) exceeds maximum allowed safety limit of 4" in result["error"]
        assert len(result["completed_steps"]) == 0

    def test_request_rate_limiting_halts_before_dispatch(self, setup_checkpoint_env):
        _, limiter = setup_checkpoint_env
        agent = VoxPilotAgent()
        agent.intent_recognizer = MagicMock()
        agent.intent_recognizer.recognize_intent.return_value = {
            "intent": "general_conversation",
            "confidence": 0.9,
            "reason": "Chatting",
        }
        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = {
            "steps": [],
            "response": "Chat response",
        }
        agent.planner = mock_planner

        # Dispatch 5 requests (requests_per_minute = 5)
        for i in range(5):
            res = agent.process_request(f"test {i}", execution_id=f"exec_rate_{i}", conversation_id=f"conv_{i}")
            assert res["execution_status"] in ("completed", "failed", "started", "planned")

        # 6th request must be rejected with status="rate_limited" before dispatch
        res_6 = agent.process_request("test 6", execution_id="exec_rate_6", conversation_id="conv_6")
        assert res_6["execution_status"] == "rate_limited"
        assert "Global request rate limit exceeded" in res_6["error"]

    def test_tool_call_budget_halts_and_preserves_completed_steps(self, setup_checkpoint_env):
        _, limiter = setup_checkpoint_env
        # tool_calls_per_execution is 3
        agent = VoxPilotAgent()
        agent.intent_recognizer = MagicMock()
        agent.intent_recognizer.recognize_intent.return_value = {
            "intent": "file_operation",
            "confidence": 0.95,
            "reason": "File operations",
        }

        # Plan with 4 tool steps
        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = {
            "steps": [
                {"action": "get_file_metadata", "parameters": {"file_path": "C:\\Users\\Thanu\\Documents\\test.txt"}},
                {"action": "create_folder", "parameters": {"folder_path": "C:\\Users\\Thanu\\Documents\\test_f"}},
                {"action": "move_mouse", "parameters": {"x": 100, "y": 100}},
                {"action": "type_text", "parameters": {"hwnd": 12345, "text": "hello"}},
            ],
            "response": "4 tools planned",
        }
        agent.planner = mock_planner

        # Mock tool executor so the first 3 succeed and 4th triggers budget rejection
        call_count = 0

        def fake_execute_step(step, step_index=0, override_confirmation_token=None):
            nonlocal call_count
            call_count += 1
            action = step.get("action", "")
            exec_id = step.get("execution_id")

            # Check budget
            allowed, reason = limiter.check_tool_budget(exec_id, action)
            if not allowed:
                return ExecutionResult(
                    step_index=step_index,
                    action=action,
                    success=False,
                    status="rate_limited",
                    message=f"Action '{action}' rejected: {reason}",
                    error=reason,
                )
            limiter.record_tool_call(exec_id, action)
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=True,
                status="completed",
                message=f"Executed {action}",
            )

        agent.tool_executor.execute_step = fake_execute_step

        result = agent.process_request("run 4 tools", execution_id="exec_budget_preserve")

        # Must halt at step 4 with rate_limited status
        assert result["execution_status"] == "rate_limited"
        # First 3 steps MUST be preserved in completed_steps!
        assert len(result["completed_steps"]) == 3
        assert len(result["execution_results"]) == 4
        assert result["execution_results"][3]["status"] == "rate_limited"

    def test_duration_budget_exceeded_blocks_resumption(self, setup_checkpoint_env):
        _, limiter = setup_checkpoint_env
        # max_duration_seconds is 2.0s
        agent = VoxPilotAgent()

        # Resuming a state where started_at was 10 seconds ago
        state: VoxPilotState = {
            "user_input": "delete sensitive file",
            "execution_id": "exec_resume_timeout",
            "execution_status": "confirmation_required",
            "current_step": 0,
            "plan": [
                {"action": "delete_file", "parameters": {"file_path": "C:\\Users\\Thanu\\Documents\\file.txt"}}
            ],
            "execution_results": [],
            "completed_steps": [],
            "failed_steps": [],
            "requires_confirmation": True,
            "started_at": time.time() - 10.0,  # 10s elapsed
        }

        res = agent.resume_execution(state, confirmation_token="dummy_token_123")
        assert res["execution_status"] == "rate_limited"
        assert "Execution duration budget exceeded" in res["error"]
