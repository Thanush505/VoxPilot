"""
Unit tests for VoxPilot Global Emergency Stop Subsystem.
Tests thread-safe latched state, operation registration/unregistration,
safe reset validation, cleanup callbacks, and global hotkey listener.
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from backend.automation.emergency_stop import (
    EmergencyStopManager,
    EmergencyStopActiveError,
    UnsafeResetError,
    GlobalHotkeyListener,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
    release_system_inputs,
)


@pytest.fixture(autouse=True)
def reset_shared_manager():
    """Ensure a clean, inactive EmergencyStopManager before and after each test."""
    manager = EmergencyStopManager()
    set_shared_emergency_stop_manager(manager)
    yield manager
    # Teardown: ensure forced reset if still active
    if manager.is_emergency_stop_active():
        manager.reset_emergency_stop(force=True)


class TestEmergencyStopManager:
    """Tests for core coordinator state machine and operation tracking."""

    def test_initial_state_inactive(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        assert not manager.is_emergency_stop_active()
        assert manager.trigger_reason is None
        assert manager.trigger_source is None
        assert manager.triggered_at is None
        assert len(manager.list_active_operations()) == 0

        status = manager.get_emergency_stop_status()
        assert status["is_active"] is False
        assert status["active_operations_count"] == 0

    def test_check_emergency_stop_when_inactive(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        # Should execute silently without raising
        manager.check_emergency_stop()

    def test_trigger_emergency_stop(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        status = manager.trigger_emergency_stop(reason="Operator pressed physical hotkey", source="hotkey")

        assert manager.is_emergency_stop_active() is True
        assert status["is_active"] is True
        assert manager.trigger_reason == "Operator pressed physical hotkey"
        assert manager.trigger_source == "hotkey"
        assert manager.triggered_at is not None

        # Verify check_emergency_stop now raises EmergencyStopActiveError
        with pytest.raises(EmergencyStopActiveError) as exc_info:
            manager.check_emergency_stop()
        assert "Operator pressed physical hotkey" in str(exc_info.value)
        assert exc_info.value.reason == "Operator pressed physical hotkey"

    def test_repeated_trigger_is_idempotent_and_safe(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.trigger_emergency_stop(reason="Initial hotkey press", source="hotkey")
        first_triggered_at = manager.triggered_at

        # Repeated trigger with new reason should not throw and should retain first trigger metadata
        res = manager.trigger_emergency_stop(reason="Secondary API trigger", source="api")
        assert manager.is_emergency_stop_active() is True
        assert manager.trigger_reason == "Initial hotkey press"
        assert manager.triggered_at == first_triggered_at

    def test_operation_registration_and_unregistration(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.register_active_operation(
            operation_id="op_123",
            execution_id="exec_abc",
            category="keyboard",
            description="Typing text into Notepad",
        )

        ops = manager.list_active_operations()
        assert len(ops) == 1
        assert ops[0]["operation_id"] == "op_123"
        assert ops[0]["execution_id"] == "exec_abc"
        assert ops[0]["category"] == "keyboard"
        assert ops[0]["status"] == "running"

        manager.unregister_active_operation("op_123")
        assert len(manager.list_active_operations()) == 0

    def test_register_operation_fails_when_latched(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.trigger_emergency_stop(reason="Emergency latch test", source="manual")

        with pytest.raises(EmergencyStopActiveError) as exc_info:
            manager.register_active_operation(
                operation_id="op_new",
                execution_id="exec_1",
                category="mouse",
                description="Click button",
            )
        assert "Cannot start operation 'op_new'" in str(exc_info.value)
        assert len(manager.list_active_operations()) == 0

    def test_unsafe_reset_rejected_with_active_operations(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.register_active_operation(
            operation_id="op_hanging",
            execution_id="exec_hang",
            category="browser",
            description="Long running navigation",
        )
        manager.trigger_emergency_stop(reason="User stopped hanging operation", source="hotkey")

        # Attempting standard reset should raise UnsafeResetError
        with pytest.raises(UnsafeResetError) as exc_info:
            manager.reset_emergency_stop(force=False)
        assert "active operations have not reached a confirmed safe terminal state" in str(exc_info.value)
        assert exc_info.value.active_count == 1
        assert manager.is_emergency_stop_active() is True

    def test_force_reset_succeeds_even_with_active_operations(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.register_active_operation(
            operation_id="op_hanging",
            execution_id="exec_hang",
            category="browser",
            description="Long running navigation",
        )
        manager.trigger_emergency_stop(reason="Forced override test", source="api")

        status = manager.reset_emergency_stop(force=True)
        assert status["is_active"] is False
        assert not manager.is_emergency_stop_active()
        assert len(manager.list_active_operations()) == 0

    def test_safe_reset_after_all_operations_unregister(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.register_active_operation(
            operation_id="op_run",
            execution_id="exec_1",
            category="file",
            description="Copying file",
        )
        manager.trigger_emergency_stop(reason="Stop triggered", source="hotkey")

        # Complete operation shutdown
        manager.unregister_active_operation("op_run")
        assert len(manager.list_active_operations()) == 0

        # Now clean reset must succeed
        status = manager.reset_emergency_stop(force=False)
        assert status["is_active"] is False
        assert not manager.is_emergency_stop_active()
        assert manager.trigger_reason is None

    def test_cleanup_callbacks_invoked_on_trigger(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        mock_cb1 = MagicMock()
        mock_cb2 = MagicMock()
        mock_failing_cb = MagicMock(side_effect=RuntimeError("Callback exploded"))

        manager.register_cleanup_callback(mock_cb1)
        manager.register_cleanup_callback(mock_failing_cb)
        manager.register_cleanup_callback(mock_cb2)

        manager.trigger_emergency_stop(reason="Testing callbacks", source="test")

        mock_cb1.assert_called_once()
        mock_failing_cb.assert_called_once()
        mock_cb2.assert_called_once()

    def test_reset_does_not_auto_resume(self, reset_shared_manager: EmergencyStopManager):
        manager = reset_shared_manager
        manager.trigger_emergency_stop(reason="Halt", source="hotkey")
        manager.reset_emergency_stop(force=True)

        # Confirm manager is simply inactive, nothing is automatically enqueued or running
        assert not manager.is_emergency_stop_active()
        assert len(manager.list_active_operations()) == 0


class TestGlobalHotkeyListener:
    """Tests for Windows Win32 hotkey listener lifecycle."""

    def test_listener_initialization_and_properties(self):
        manager = EmergencyStopManager()
        listener = GlobalHotkeyListener(manager=manager)
        assert not listener.is_running
        assert not listener.is_registered

    def test_listener_graceful_stop_when_not_started(self):
        manager = EmergencyStopManager()
        listener = GlobalHotkeyListener(manager=manager)
        # Calling stop before start should not raise
        listener.stop()
        assert not listener.is_running

    def test_listener_starts_and_stops_cleanly(self):
        manager = EmergencyStopManager()
        listener = GlobalHotkeyListener(manager=manager)
        try:
            listener.start()
            time.sleep(0.1)
            assert listener.is_running
            # Registration status on Windows 11 desktop
            reg_status = listener.is_registered
            assert isinstance(reg_status, bool)
        finally:
            listener.stop()
            assert not listener.is_running

    def test_release_system_inputs_executes_safely(self):
        # Call release_system_inputs and ensure no unhandled exception
        release_system_inputs()
