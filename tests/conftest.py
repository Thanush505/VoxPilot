"""
Global Pytest Configuration and Fixtures for VoxPilot.
Ensures that shared in-memory state (rate limiters, emergency stop latch)
is cleanly reset before and after each test.
"""

from __future__ import annotations

import pytest
from backend.agent.rate_limiter import reset_shared_rate_limiter, get_shared_rate_limiter
from backend.automation.emergency_stop import get_shared_emergency_stop_manager


@pytest.fixture(autouse=True)
def clean_shared_state_between_tests():
    """Ensure clean rate limiter and inactive emergency stop between every test."""
    limiter = reset_shared_rate_limiter()
    manager = get_shared_emergency_stop_manager()
    yield
    limiter.reset()
    if manager.is_emergency_stop_active():
        for op in manager.list_active_operations():
            manager.unregister_active_operation(op["operation_id"])
        manager.reset_emergency_stop()

