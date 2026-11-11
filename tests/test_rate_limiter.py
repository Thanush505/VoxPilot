"""
Unit tests for VoxPilot Sliding-Window Rate Limiter.
Tests request throttling, conversation-level limits, concurrency slot management,
and sliding-window timestamp expiration.
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import patch

from backend.agent.rate_limiter import (
    RateLimiter,
    RateLimiterConfig,
    RateLimitExceededError,
    ConcurrentExecutionLimitError,
    get_shared_rate_limiter,
    set_shared_rate_limiter,
    reset_shared_rate_limiter,
)


@pytest.fixture(autouse=True)
def clean_rate_limiter():
    """Ensure a clean, isolated rate limiter for each test."""
    limiter = RateLimiter(RateLimiterConfig(
        requests_per_minute=5,
        max_concurrent_executions=2,
        conversation_requests_per_minute=3,
        tool_calls_per_minute=10,
        tool_calls_per_execution=5,
        same_tool_per_execution=3,
        max_duration_seconds=5.0,
    ))
    set_shared_rate_limiter(limiter)
    yield limiter
    limiter.reset()


class TestRateLimiterConfiguration:
    """Tests environment variable parsing and configuration defaults."""

    def test_default_config(self):
        config = RateLimiterConfig()
        assert config.requests_per_minute == 20
        assert config.max_concurrent_executions == 2
        assert config.conversation_requests_per_minute == 10
        assert config.tool_calls_per_execution == 15
        assert config.tool_calls_per_minute == 60
        assert config.same_tool_per_execution == 5
        assert config.max_steps == 10
        assert config.max_duration_seconds == 120.0
        assert config.max_browser_navigations == 5
        assert config.max_downloads == 3

    def test_from_env_overrides(self, monkeypatch):
        monkeypatch.setenv("VOXPILOT_RATE_LIMIT_REQUESTS_PER_MINUTE", "50")
        monkeypatch.setenv("VOXPILOT_RATE_LIMIT_CONCURRENT_EXECUTIONS", "4")
        monkeypatch.setenv("VOXPILOT_BUDGET_MAX_DURATION_SECONDS", "300.5")

        config = RateLimiterConfig.from_env()
        assert config.requests_per_minute == 50
        assert config.max_concurrent_executions == 4
        assert config.max_duration_seconds == 300.5


class TestRequestRateLimiting:
    """Tests global and conversation-level request sliding windows."""

    def test_global_requests_within_limit(self, clean_rate_limiter: RateLimiter):
        limiter = clean_rate_limiter
        for _ in range(5):
            allowed, reason = limiter.check_request_rate_limit()
            assert allowed is True
            assert reason is None
            limiter.record_request()

        # 6th request should be rejected
        allowed, reason = limiter.check_request_rate_limit()
        assert allowed is False
        assert "Global request rate limit exceeded" in reason

    def test_conversation_requests_within_limit(self, clean_rate_limiter: RateLimiter):
        limiter = clean_rate_limiter
        conv_id = "conv_abc"

        for _ in range(3):
            allowed, reason = limiter.check_request_rate_limit(conversation_id=conv_id)
            assert allowed is True
            limiter.record_request(conversation_id=conv_id)

        # 4th request on same conversation rejected even if global limit (5) has room
        allowed, reason = limiter.check_request_rate_limit(conversation_id=conv_id)
        assert allowed is False
        assert f"Conversation request rate limit exceeded for '{conv_id}'" in reason

        # Another conversation is still allowed
        other_allowed, other_reason = limiter.check_request_rate_limit(conversation_id="conv_xyz")
        assert other_allowed is True

    def test_sliding_window_pruning(self, clean_rate_limiter: RateLimiter):
        limiter = clean_rate_limiter
        # Simulate requests 65 seconds ago
        old_time = time.time() - 65.0
        with limiter._lock:
            for _ in range(5):
                limiter._global_request_timestamps.append(old_time)

        # Because they are older than 60s, a new request should be admitted
        allowed, reason = limiter.check_request_rate_limit()
        assert allowed is True


class TestConcurrentExecutionLimiting:
    """Tests slot acquisition and release for concurrent executions."""

    def test_concurrency_slots(self, clean_rate_limiter: RateLimiter):
        limiter = clean_rate_limiter
        # Slot 1
        ok1, reason1 = limiter.acquire_execution_slot("exec_1")
        assert ok1 is True
        assert limiter.is_execution_active("exec_1") is True

        # Slot 2
        ok2, reason2 = limiter.acquire_execution_slot("exec_2")
        assert ok2 is True
        assert limiter.is_execution_active("exec_2") is True

        # Slot 3: exceeds max_concurrent_executions (2)
        ok3, reason3 = limiter.acquire_execution_slot("exec_3")
        assert ok3 is False
        assert "Concurrent execution limit reached" in reason3

        # Release Slot 1 -> Slot 3 can now be acquired
        limiter.release_execution_slot("exec_1")
        assert limiter.is_execution_active("exec_1") is False

        ok3_retry, _ = limiter.acquire_execution_slot("exec_3")
        assert ok3_retry is True
        assert limiter.is_execution_active("exec_3") is True

    def test_reacquiring_same_execution_slot_is_idempotent(self, clean_rate_limiter: RateLimiter):
        limiter = clean_rate_limiter
        ok1, _ = limiter.acquire_execution_slot("exec_repeat")
        assert ok1 is True

        ok2, _ = limiter.acquire_execution_slot("exec_repeat")
        assert ok2 is True

