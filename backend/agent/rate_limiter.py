"""
VoxPilot - Automation Rate Limiting, Safety Budgets, and Sliding-Window Throttling
Provides in-process, thread-safe rate limiting and resource budgets for agent executions,
tool invocations, concurrent requests, and resource-intensive actions.
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Any, List

logger = logging.getLogger(__name__)


# ==============================================================================
# Rate Limiting & Safety Budget Exceptions
# ==============================================================================

class RateLimitError(Exception):
    """Base exception for rate limiting and budget exhaustion."""
    pass


class RateLimitExceededError(RateLimitError):
    """Raised when request or tool call frequency exceeds allowed thresholds."""
    def __init__(self, message: str, limit: int, window_seconds: float = 60.0, retry_after: float = 0.0):
        super().__init__(message)
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after


class ExecutionBudgetExceededError(RateLimitError):
    """Raised when an execution session exceeds allowed steps, duration, or tool quotas."""
    def __init__(self, message: str, budget_type: str, current_value: Any, max_allowed: Any):
        super().__init__(message)
        self.budget_type = budget_type
        self.current_value = current_value
        self.max_allowed = max_allowed


class ConcurrentExecutionLimitError(RateLimitError):
    """Raised when concurrent active agent executions exceed maximum capacity."""
    def __init__(self, message: str, current_count: int, max_concurrent: int):
        super().__init__(message)
        self.current_count = current_count
        self.max_concurrent = max_concurrent


# ==============================================================================
# Configuration Data Model
# ==============================================================================

@dataclass
class RateLimiterConfig:
    """Configurable rate limits and resource budgets read from environment variables."""
    # Request limits
    requests_per_minute: int = 20
    max_concurrent_executions: int = 2
    conversation_requests_per_minute: int = 10

    # Tool invocation limits
    tool_calls_per_execution: int = 15
    tool_calls_per_minute: int = 60
    same_tool_per_execution: int = 5

    # Resource budgets per execution
    max_steps: int = 10
    max_duration_seconds: float = 120.0
    max_browser_navigations: int = 5
    max_downloads: int = 3

    @classmethod
    def from_env(cls) -> "RateLimiterConfig":
        """Instantiate configuration with environment variable overrides."""
        def get_int(key: str, default: int) -> int:
            val = os.getenv(key)
            if val is not None:
                try:
                    return int(val)
                except ValueError:
                    logger.warning(f"Invalid integer for env var {key}='{val}', using default {default}")
            return default

        def get_float(key: str, default: float) -> float:
            val = os.getenv(key)
            if val is not None:
                try:
                    return float(val)
                except ValueError:
                    logger.warning(f"Invalid float for env var {key}='{val}', using default {default}")
            return default

        return cls(
            requests_per_minute=get_int("VOXPILOT_RATE_LIMIT_REQUESTS_PER_MINUTE", 20),
            max_concurrent_executions=get_int("VOXPILOT_RATE_LIMIT_CONCURRENT_EXECUTIONS", 2),
            conversation_requests_per_minute=get_int("VOXPILOT_RATE_LIMIT_CONVERSATION_PER_MINUTE", 10),
            tool_calls_per_execution=get_int("VOXPILOT_LIMIT_TOOL_CALLS_PER_EXECUTION", 15),
            tool_calls_per_minute=get_int("VOXPILOT_LIMIT_TOOL_CALLS_PER_MINUTE", 60),
            same_tool_per_execution=get_int("VOXPILOT_LIMIT_SAME_TOOL_PER_EXECUTION", 5),
            max_steps=get_int("VOXPILOT_BUDGET_MAX_STEPS", 10),
            max_duration_seconds=get_float("VOXPILOT_BUDGET_MAX_DURATION_SECONDS", 120.0),
            max_browser_navigations=get_int("VOXPILOT_BUDGET_MAX_BROWSER_NAVIGATIONS", 5),
            max_downloads=get_int("VOXPILOT_BUDGET_MAX_DOWNLOADS", 3),
        )


# ==============================================================================
# Per-Execution Resource Tracker
# ==============================================================================

@dataclass
class ExecutionBudgetTracker:
    """Tracks resource consumption and tool counts for a single execution session."""
    execution_id: str
    started_at: float = field(default_factory=time.time)
    total_tool_calls: int = 0
    action_counts: Dict[str, int] = field(default_factory=lambda: collections.defaultdict(int))
    browser_navigations: int = 0
    downloads: int = 0

    def record_action(self, action: str) -> None:
        """Increment counters for the dispatched action."""
        self.total_tool_calls += 1
        self.action_counts[action] += 1
        if action in ("navigate_url", "navigate_browser", "open_browser_url"):
            self.browser_navigations += 1
        elif action in ("download_file", "browser_download"):
            self.downloads += 1

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at


# ==============================================================================
# Sliding-Window Rate Limiter Engine
# ==============================================================================

class RateLimiter:
    """Thread-safe, in-process rate limiter and execution budget enforcer."""

    def __init__(self, config: Optional[RateLimiterConfig] = None):
        self.config = config or RateLimiterConfig.from_env()
        self._lock = threading.Lock()

        # Sliding window timestamps (seconds since epoch)
        self._global_request_timestamps: collections.deque[float] = collections.deque()
        self._conversation_request_timestamps: Dict[str, collections.deque[float]] = {}
        self._global_tool_timestamps: collections.deque[float] = collections.deque()

        # Active executions tracking: execution_id -> start_time
        self._active_executions: Dict[str, float] = {}

        # Per-execution budget tracker: execution_id -> ExecutionBudgetTracker
        self._execution_trackers: Dict[str, ExecutionBudgetTracker] = {}

    # --------------------------------------------------------------------------
    # Sliding-Window Pruning Helpers
    # --------------------------------------------------------------------------

    @staticmethod
    def _prune_deque(dq: collections.deque[float], cutoff: float) -> None:
        """Remove timestamps older than cutoff from the left of the deque."""
        while dq and dq[0] < cutoff:
            dq.popleft()

    # --------------------------------------------------------------------------
    # Request Rate Limiting
    # --------------------------------------------------------------------------

    def check_request_rate_limit(self, conversation_id: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Verify whether a new agent request is permitted within global and conversation limits.

        Returns:
            Tuple[bool, Optional[str]]: (allowed, rejection_reason)
        """
        now = time.time()
        cutoff_60s = now - 60.0

        with self._lock:
            # 1. Global requests per minute
            self._prune_deque(self._global_request_timestamps, cutoff_60s)
            if len(self._global_request_timestamps) >= self.config.requests_per_minute:
                retry_after = round(60.0 - (now - self._global_request_timestamps[0]), 1)
                reason = (
                    f"Global request rate limit exceeded: {len(self._global_request_timestamps)}/"
                    f"{self.config.requests_per_minute} requests in the last minute. "
                    f"Please retry in {max(0.1, retry_after)}s."
                )
                logger.warning(reason)
                return False, reason

            # 2. Per-conversation requests per minute
            if conversation_id:
                conv_dq = self._conversation_request_timestamps.setdefault(
                    conversation_id, collections.deque()
                )
                self._prune_deque(conv_dq, cutoff_60s)
                if len(conv_dq) >= self.config.conversation_requests_per_minute:
                    retry_after = round(60.0 - (now - conv_dq[0]), 1)
                    reason = (
                        f"Conversation request rate limit exceeded for '{conversation_id}': "
                        f"{len(conv_dq)}/{self.config.conversation_requests_per_minute} requests in the last minute. "
                        f"Please retry in {max(0.1, retry_after)}s."
                    )
                    logger.warning(reason)
                    return False, reason

            return True, None

    def record_request(self, conversation_id: Optional[str] = None) -> None:
        """Record the timestamp of an admitted request."""
        now = time.time()
        with self._lock:
            self._global_request_timestamps.append(now)
            if conversation_id:
                conv_dq = self._conversation_request_timestamps.setdefault(
                    conversation_id, collections.deque()
                )
                conv_dq.append(now)

    # --------------------------------------------------------------------------
    # Concurrent Execution Slots
    # --------------------------------------------------------------------------

    def acquire_execution_slot(self, execution_id: str) -> Tuple[bool, Optional[str]]:
        """Attempt to acquire a concurrent execution slot.

        Returns:
            Tuple[bool, Optional[str]]: (acquired, rejection_reason)
        """
        with self._lock:
            if execution_id in self._active_executions:
                # Already acquired for this execution session
                return True, None

            if len(self._active_executions) >= self.config.max_concurrent_executions:
                reason = (
                    f"Concurrent execution limit reached: {len(self._active_executions)}/"
                    f"{self.config.max_concurrent_executions} executions currently running. "
                    "Wait for active executions to complete before starting new ones."
                )
                logger.warning(reason)
                return False, reason

            now = time.time()
            self._active_executions[execution_id] = now
            self._execution_trackers[execution_id] = ExecutionBudgetTracker(
                execution_id=execution_id,
                started_at=now,
            )
            return True, None

    def release_execution_slot(self, execution_id: str) -> None:
        """Release the concurrent execution slot for a completed or aborted execution."""
        with self._lock:
            self._active_executions.pop(execution_id, None)

    def is_execution_active(self, execution_id: str) -> bool:
        """Check if an execution currently holds an active execution slot."""
        with self._lock:
            return execution_id in self._active_executions

    # --------------------------------------------------------------------------
    # Tool-Call Budgets and Checkpoints
    # --------------------------------------------------------------------------

    def check_tool_budget(
        self,
        execution_id: Optional[str],
        action: str,
        current_duration: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Check whether a tool invocation is within all safety budgets and rate limits.

        Checks:
        1. Global tool calls per minute (sliding window <= 60).
        2. Execution wall-clock duration (<= max_duration_seconds).
        3. Execution total tool calls (<= max_tool_calls_per_execution).
        4. Same-tool invocation count in this execution (<= same_tool_per_execution).
        5. Browser navigations in this execution (<= max_browser_navigations).
        6. Downloads in this execution (<= max_downloads).

        Returns:
            Tuple[bool, Optional[str]]: (allowed, rejection_reason)
        """
        now = time.time()
        cutoff_60s = now - 60.0

        with self._lock:
            # 1. Global tool calls per minute
            self._prune_deque(self._global_tool_timestamps, cutoff_60s)
            if len(self._global_tool_timestamps) >= self.config.tool_calls_per_minute:
                reason = (
                    f"Global tool call rate limit exceeded: {len(self._global_tool_timestamps)}/"
                    f"{self.config.tool_calls_per_minute} tool invocations in the last minute. "
                    "Throttling tool execution to protect host stability."
                )
                return False, reason

            # If no execution_id provided, execution-level budgets cannot be checked
            if not execution_id:
                return True, None

            tracker = self._execution_trackers.get(execution_id)
            if not tracker:
                # Execution tracker not found; initialize one
                tracker = ExecutionBudgetTracker(execution_id=execution_id, started_at=now)
                self._execution_trackers[execution_id] = tracker

            # 2. Execution wall-clock duration
            duration = current_duration if current_duration is not None else tracker.elapsed_seconds
            if duration > self.config.max_duration_seconds:
                reason = (
                    f"Execution wall-clock budget exceeded: {round(duration, 1)}s elapsed "
                    f"(maximum allowed: {self.config.max_duration_seconds}s). Halting execution."
                )
                return False, reason

            # 3. Total tool calls for this execution
            if tracker.total_tool_calls >= self.config.tool_calls_per_execution:
                reason = (
                    f"Execution tool call budget exceeded: {tracker.total_tool_calls}/"
                    f"{self.config.tool_calls_per_execution} tool calls reached for this execution session."
                )
                return False, reason

            # 4. Same-tool repetition limit
            same_tool_count = tracker.action_counts.get(action, 0)
            if same_tool_count >= self.config.same_tool_per_execution:
                reason = (
                    f"Repeated tool limit exceeded for '{action}': called {same_tool_count}/"
                    f"{self.config.same_tool_per_execution} times in this execution session. "
                    "Halting to prevent infinite loops."
                )
                return False, reason

            # 5. Browser navigations limit
            if action in ("navigate_url", "navigate_browser", "open_browser_url"):
                if tracker.browser_navigations >= self.config.max_browser_navigations:
                    reason = (
                        f"Browser navigation budget exceeded: {tracker.browser_navigations}/"
                        f"{self.config.max_browser_navigations} navigations performed in this session."
                    )
                    return False, reason

            # 6. Downloads limit
            if action in ("download_file", "browser_download"):
                if tracker.downloads >= self.config.max_downloads:
                    reason = (
                        f"Download budget exceeded: {tracker.downloads}/"
                        f"{self.config.max_downloads} downloads performed in this session."
                    )
                    return False, reason

            return True, None

    def record_tool_call(self, execution_id: Optional[str], action: str) -> None:
        """Record the occurrence of a tool invocation attempt in global and execution budgets."""
        now = time.time()
        with self._lock:
            self._global_tool_timestamps.append(now)
            if execution_id:
                tracker = self._execution_trackers.get(execution_id)
                if not tracker:
                    tracker = ExecutionBudgetTracker(execution_id=execution_id, started_at=now)
                    self._execution_trackers[execution_id] = tracker
                tracker.record_action(action)

    def check_execution_budget(
        self,
        execution_id: str,
        elapsed_seconds: Optional[float] = None,
        step_count: Optional[int] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Check high-level execution budgets: max steps and max wall-clock duration."""
        with self._lock:
            if step_count is not None and step_count > self.config.max_steps:
                return False, (
                    f"Plan step budget exceeded: {step_count} steps "
                    f"(maximum allowed: {self.config.max_steps})."
                )

            if elapsed_seconds is not None and elapsed_seconds > self.config.max_duration_seconds:
                return False, (
                    f"Execution duration budget exceeded: {round(elapsed_seconds, 1)}s "
                    f"(maximum allowed: {self.config.max_duration_seconds}s)."
                )

            tracker = self._execution_trackers.get(execution_id)
            if tracker:
                if tracker.elapsed_seconds > self.config.max_duration_seconds:
                    return False, (
                        f"Execution duration budget exceeded: {round(tracker.elapsed_seconds, 1)}s "
                        f"(maximum allowed: {self.config.max_duration_seconds}s)."
                    )

            return True, None

    def get_execution_stats(self, execution_id: str) -> Dict[str, Any]:
        """Retrieve resource consumption stats for an execution session."""
        with self._lock:
            tracker = self._execution_trackers.get(execution_id)
            if not tracker:
                return {
                    "execution_id": execution_id,
                    "is_active": execution_id in self._active_executions,
                    "total_tool_calls": 0,
                    "elapsed_seconds": 0.0,
                    "action_counts": {},
                }
            return {
                "execution_id": execution_id,
                "is_active": execution_id in self._active_executions,
                "total_tool_calls": tracker.total_tool_calls,
                "browser_navigations": tracker.browser_navigations,
                "downloads": tracker.downloads,
                "elapsed_seconds": round(tracker.elapsed_seconds, 2),
                "action_counts": dict(tracker.action_counts),
            }

    def reset(self) -> None:
        """Clear all active executions, sliding window timestamps, and trackers."""
        with self._lock:
            self._global_request_timestamps.clear()
            self._conversation_request_timestamps.clear()
            self._global_tool_timestamps.clear()
            self._active_executions.clear()
            self._execution_trackers.clear()


# ==============================================================================
# Shared Singleton Management
# ==============================================================================

_SHARED_RATE_LIMITER: Optional[RateLimiter] = None
_RATE_LIMITER_LOCK = threading.Lock()


def get_shared_rate_limiter() -> RateLimiter:
    """Retrieve or initialize the active shared RateLimiter singleton."""
    global _SHARED_RATE_LIMITER
    with _RATE_LIMITER_LOCK:
        if _SHARED_RATE_LIMITER is None:
            _SHARED_RATE_LIMITER = RateLimiter()
        return _SHARED_RATE_LIMITER


def set_shared_rate_limiter(limiter: RateLimiter) -> None:
    """Explicitly inject a RateLimiter singleton (e.g. for testing)."""
    global _SHARED_RATE_LIMITER
    with _RATE_LIMITER_LOCK:
        _SHARED_RATE_LIMITER = limiter


def reset_shared_rate_limiter() -> RateLimiter:
    """Reset and reinitialize the shared RateLimiter singleton."""
    global _SHARED_RATE_LIMITER
    with _RATE_LIMITER_LOCK:
        _SHARED_RATE_LIMITER = RateLimiter()
        return _SHARED_RATE_LIMITER

