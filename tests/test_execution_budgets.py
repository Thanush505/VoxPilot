"""
Unit tests for VoxPilot Execution Budgets and Safety Limits.
Tests tool call limits per execution, same-tool repetition throttling,
browser navigation and download budgets, wall-clock duration, and step counts.
"""

from __future__ import annotations

import time
import pytest

from backend.agent.rate_limiter import (
    RateLimiter,
    RateLimiterConfig,
    set_shared_rate_limiter,
)


@pytest.fixture(autouse=True)
def setup_budget_limiter():
    """Configure strict budgets for testing."""
    config = RateLimiterConfig(
        requests_per_minute=20,
        max_concurrent_executions=5,
        conversation_requests_per_minute=20,
        tool_calls_per_execution=4,
        tool_calls_per_minute=50,
        same_tool_per_execution=2,
        max_steps=5,
        max_duration_seconds=3.0,
        max_browser_navigations=2,
        max_downloads=1,
    )
    limiter = RateLimiter(config=config)
    set_shared_rate_limiter(limiter)
    yield limiter
    limiter.reset()


class TestExecutionBudgets:
    """Tests execution-level resource thresholds."""

    def test_tool_calls_per_execution_budget(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_budget_1"
        limiter.acquire_execution_slot(exec_id)

        # 4 tool calls are permitted (using different actions)
        actions = ["type_text", "press_key", "click_mouse", "get_file_metadata"]
        for action in actions:
            allowed, reason = limiter.check_tool_budget(exec_id, action)
            assert allowed is True
            assert reason is None
            limiter.record_tool_call(exec_id, action)

        # 5th tool call exceeds budget of 4
        allowed, reason = limiter.check_tool_budget(exec_id, "move_mouse")
        assert allowed is False
        assert "Execution tool call budget exceeded" in reason
        assert "4/4" in reason

    def test_same_tool_repetition_limit(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_repeat_tool"
        limiter.acquire_execution_slot(exec_id)

        # same_tool_per_execution is 2
        for _ in range(2):
            allowed, reason = limiter.check_tool_budget(exec_id, "type_text")
            assert allowed is True
            limiter.record_tool_call(exec_id, "type_text")

        # 3rd invocation of same tool should be rejected to prevent infinite loops
        allowed, reason = limiter.check_tool_budget(exec_id, "type_text")
        assert allowed is False
        assert "Repeated tool limit exceeded for 'type_text'" in reason
        assert "2/2" in reason

        # But a DIFFERENT tool is still allowed (within total budget)
        diff_allowed, diff_reason = limiter.check_tool_budget(exec_id, "press_key")
        assert diff_allowed is True

    def test_browser_navigation_budget(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_nav_budget"
        limiter.acquire_execution_slot(exec_id)

        # max_browser_navigations is 2
        for _ in range(2):
            allowed, reason = limiter.check_tool_budget(exec_id, "navigate_url")
            assert allowed is True
            limiter.record_tool_call(exec_id, "navigate_url")

        # 3rd navigation rejected
        allowed, reason = limiter.check_tool_budget(exec_id, "navigate_url")
        assert allowed is False
        # Either repeated tool or browser navigation budget exceeded
        assert "budget exceeded" in reason or "limit exceeded" in reason

    def test_download_budget(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_dl_budget"
        limiter.acquire_execution_slot(exec_id)

        # max_downloads is 1
        allowed, reason = limiter.check_tool_budget(exec_id, "download_file")
        assert allowed is True
        limiter.record_tool_call(exec_id, "download_file")

        # 2nd download rejected
        allowed, reason = limiter.check_tool_budget(exec_id, "download_file")
        assert allowed is False
        assert "budget exceeded" in reason or "limit exceeded" in reason

    def test_duration_budget_exceeded(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_duration_budget"
        limiter.acquire_execution_slot(exec_id)

        # Duration budget is 3.0s; pass elapsed_seconds = 4.5s
        allowed, reason = limiter.check_tool_budget(exec_id, "type_text", current_duration=4.5)
        assert allowed is False
        assert "Execution wall-clock budget exceeded" in reason
        assert "4.5s elapsed" in reason

    def test_execution_budget_step_count(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_steps"
        # max_steps is 5
        allowed_5, _ = limiter.check_execution_budget(exec_id, step_count=5)
        assert allowed_5 is True

        allowed_6, reason_6 = limiter.check_execution_budget(exec_id, step_count=6)
        assert allowed_6 is False
        assert "Plan step budget exceeded: 6 steps" in reason_6

    def test_get_execution_stats(self, setup_budget_limiter: RateLimiter):
        limiter = setup_budget_limiter
        exec_id = "exec_stats"
        limiter.acquire_execution_slot(exec_id)
        limiter.record_tool_call(exec_id, "type_text")
        limiter.record_tool_call(exec_id, "navigate_url")

        stats = limiter.get_execution_stats(exec_id)
        assert stats["execution_id"] == exec_id
        assert stats["is_active"] is True
        assert stats["total_tool_calls"] == 2
        assert stats["browser_navigations"] == 1
        assert stats["action_counts"]["type_text"] == 1
        assert stats["action_counts"]["navigate_url"] == 1

