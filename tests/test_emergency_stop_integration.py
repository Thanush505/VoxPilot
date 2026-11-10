"""
Integration tests for VoxPilot controllers (keyboard, mouse, browser) and ToolExecutor
under emergency stop conditions.
Verifies modifier release, button release, session cleanup callbacks, and ToolExecutor cancellation.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.automation.emergency_stop import (
    EmergencyStopManager,
    EmergencyStopActiveError,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
)
from backend.automation.keyboard_controller import (
    KeyboardController,
    FocusLostError,
)
from backend.automation.mouse_controller import MouseController
from backend.automation.browser_controller import BrowserController
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import ToolRegistry, ToolDefinition
from backend.automation.confirmation_manager import ConfirmationManager
from pydantic import BaseModel, Field


class SimpleParam(BaseModel):
    text: str = Field("sample", description="text")


@pytest.fixture(autouse=True)
def setup_integration_env():
    """Ensure clean emergency stop manager before and after each integration test."""
    manager = EmergencyStopManager()
    set_shared_emergency_stop_manager(manager)
    yield manager
    if manager.is_emergency_stop_active():
        manager.reset_emergency_stop(force=True)


class TestKeyboardControllerEmergencyStop:
    """Verifies emergency stop behavior in KeyboardController."""

    def test_keyboard_type_text_aborts_immediately_when_stop_active(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Operator halt", source="hotkey")

        mock_wm = MagicMock()
        mock_wm.validate_window_ownership.return_value = True
        mock_wm.is_window_responsive.return_value = True
        mock_wm.activate_window.return_value = True

        controller = KeyboardController(window_manager=mock_wm)

        with patch.object(controller, "release_all_modifiers") as mock_rel:
            with pytest.raises(EmergencyStopActiveError) as exc_info:
                controller.type_text("Hello World", target_hwnd=12345)
            assert "emergency stop is active" in str(exc_info.value).lower()
            mock_rel.assert_called_once()

    def test_keyboard_type_text_chunk_aborts_mid_typing(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        mock_wm = MagicMock()
        mock_wm.validate_window_ownership.return_value = True
        mock_wm.is_window_responsive.return_value = True
        mock_wm.activate_window.return_value = True

        controller = KeyboardController(window_manager=mock_wm)

        # Mock SendInput to trigger emergency stop after first chunk
        call_count = 0

        def send_inputs_hook(inputs):
            nonlocal call_count
            call_count += 1
            if call_count >= 10:  # after several chars in first chunk
                manager.trigger_emergency_stop(reason="Mid-chunk hotkey abort", source="hotkey")
            return len(inputs)

        controller._send_inputs = send_inputs_hook

        # Type long text (>50 chars = multiple chunks of 50)
        long_text = "A" * 120

        with patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=12345):
            with patch.object(controller, "release_all_modifiers") as mock_rel:
                with pytest.raises(EmergencyStopActiveError) as exc_info:
                    controller.type_text(long_text, target_hwnd=12345, interval=0.01)
                assert "Keyboard typing aborted after" in str(exc_info.value)
                mock_rel.assert_called_once()

    def test_keyboard_press_key_aborts_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Hotkey halt", source="hotkey")

        controller = KeyboardController()
        with pytest.raises(EmergencyStopActiveError):
            controller.press_key("enter", target_hwnd=12345)

    def test_keyboard_send_shortcut_aborts_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Shortcut halt", source="hotkey")

        controller = KeyboardController()
        with pytest.raises(EmergencyStopActiveError):
            controller.send_shortcut("ctrl+c", target_hwnd=12345)


class TestMouseControllerEmergencyStop:
    """Verifies emergency stop behavior in MouseController."""

    def test_mouse_move_to_aborts_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Mouse move halt", source="hotkey")

        controller = MouseController()
        with pytest.raises(EmergencyStopActiveError):
            controller.move_to(100, 100)

    def test_mouse_click_aborts_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Mouse click halt", source="hotkey")

        controller = MouseController()
        with pytest.raises(EmergencyStopActiveError):
            controller.click(100, 100, target_hwnd=12345)

    def test_mouse_scroll_aborts_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Mouse scroll halt", source="hotkey")

        controller = MouseController()
        with pytest.raises(EmergencyStopActiveError):
            controller.scroll(120, target_hwnd=12345)

    def test_mouse_release_all_buttons_calls_system_release(self):
        controller = MouseController()
        with patch("backend.automation.mouse_controller.release_system_inputs") as mock_rel:
            controller.release_all_buttons()
            mock_rel.assert_called_once()


class TestBrowserControllerEmergencyStop:
    """Verifies browser controller emergency stop checks and session cleanup callback."""

    def test_browser_methods_abort_on_emergency_stop(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        manager.trigger_emergency_stop(reason="Browser halt", source="hotkey")

        controller = BrowserController(headless=True)

        with pytest.raises(EmergencyStopActiveError):
            controller.start_session()

        with pytest.raises(EmergencyStopActiveError):
            controller.navigate("https://example.com")

        with pytest.raises(EmergencyStopActiveError):
            controller.click_element("#btn")

        with pytest.raises(EmergencyStopActiveError):
            controller.enter_text("#input", "text")

        with pytest.raises(EmergencyStopActiveError):
            controller.download_file(url="https://example.com/test.csv")

    def test_browser_emergency_cleanup_callback_closes_active_session(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        controller = BrowserController(headless=True)

        # Mock browser session running
        with patch.object(controller, "close_session") as mock_close:
            with patch.object(BrowserController, "is_running", return_value=True):
                # Trigger emergency stop: should execute registered cleanup callbacks
                manager.trigger_emergency_stop(reason="Closing running browser", source="hotkey")
                mock_close.assert_called_once()


class TestToolExecutorEmergencyStopLifecycle:
    """Verifies ToolExecutor active operation tracking and cancelled status."""

    def test_tool_executor_operation_registration_and_cleanup(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        reg = ToolRegistry(register_defaults=False)
        cm = ConfirmationManager()

        recorded_ops = []

        def test_tool(param: SimpleParam):
            # Check tracked active operations while running
            active = manager.get_active_operations()
            recorded_ops.extend(active)
            return {"success": True, "message": "Success"}

        reg.register(
            ToolDefinition(
                name="test_tool",
                category="testing",
                description="Test tool",
                input_schema=SimpleParam,
                func=test_tool,
            )
        )

        executor = ToolExecutor(registry=reg, confirmation_manager=cm)
        result = executor.execute_step(
            step={"action": "test_tool", "parameters": {"text": "hello"}},
            step_index=0,
        )

        assert result["success"] is True
        assert len(recorded_ops) == 1
        assert recorded_ops[0]["category"] == "testing"
        assert recorded_ops[0]["description"] == "test_tool"
        # After execution completes, operation unregisters
        assert len(manager.get_active_operations()) == 0

    def test_tool_executor_returns_cancelled_when_emergency_stop_triggered_inside_tool(
        self, setup_integration_env: EmergencyStopManager
    ):
        manager = setup_integration_env
        reg = ToolRegistry(register_defaults=False)
        cm = ConfirmationManager()

        def aborting_tool(param: SimpleParam):
            manager.trigger_emergency_stop(reason="Inside tool abort", source="hotkey")
            # Next controller call would raise EmergencyStopActiveError
            manager.check_emergency_stop()
            return {"success": True}

        reg.register(
            ToolDefinition(
                name="aborting_tool",
                category="testing",
                description="Aborting tool",
                input_schema=SimpleParam,
                func=aborting_tool,
            )
        )

        executor = ToolExecutor(registry=reg, confirmation_manager=cm)
        result = executor.execute_step(
            step={"action": "aborting_tool", "parameters": {}},
            step_index=0,
        )

        assert result["success"] is False
        assert result["status"] == "cancelled"
        assert "cancelled by emergency stop" in result["message"].lower()
        # Even though tool was aborted, operation unregisters cleanly in finally
        assert len(manager.get_active_operations()) == 0
