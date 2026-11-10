"""
VoxPilot - Mouse Automation Subsystem Unit Tests
Tests pointer movement, multi-monitor virtual screen bounds (including negative coordinates),
window-relative coordinate conversion, target window bounds verification, button validation,
click count limits, scroll validation, focus loss handling, UIPI detection, and tool schemas.
"""

import pytest
from unittest.mock import MagicMock, patch, call

from backend.automation.window_manager import (
    WindowInfo,
    WindowManager,
    WindowNotFoundError,
    WindowNotAllowedError,
)
from backend.automation.mouse_controller import (
    MouseController,
    MouseControllerError,
    CoordinatesOutOfBoundsError,
    CoordinatesOutsideWindowError,
    InvalidMouseButtonError,
    InvalidClickCountError,
    WindowStateError,
    TargetNotActiveError,
    ActivationRestrictedError,
    TargetWindowUnresponsiveError,
    InputFailedError,
    UIPIRestrictedError,
    InvalidInputError,
    BUTTON_FLAGS,
    MIN_SCROLL_AMOUNT,
    MAX_SCROLL_AMOUNT,
)
from backend.tools.mouse import (
    MoveMouseInput,
    ClickMouseInput,
    ScrollMouseInput,
    MouseActionResult,
    move_mouse_tool,
    click_mouse_tool,
    scroll_mouse_tool,
)


@pytest.fixture
def mock_wm():
    """Create a mock WindowManager where HWND 2001 is a normal Notepad window at (100, 100, 500, 400)."""
    wm = MagicMock(spec=WindowManager)
    wm.validate_window_ownership.return_value = (
        "notepad",
        "Notepad",
        1234,
        "notepad.exe",
        r"C:\Windows\System32\notepad.exe",
    )
    info = WindowInfo(
        hwnd=2001,
        title="Untitled - Notepad",
        app_id="notepad",
        display_name="Notepad",
        pid=1234,
        process_name="notepad.exe",
        executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True,
        is_minimized=False,
        is_maximized=False,
        is_responsive=True,
        rect={"left": 100, "top": 100, "right": 500, "bottom": 400},
    )
    wm.get_window_info.return_value = info
    wm.is_window_responsive.return_value = True
    wm.activate_window.return_value = True
    return wm


# ==============================================================================
# 1. Coordinate Validation & Virtual Desktop Bounds
# ==============================================================================

@patch.object(MouseController, "_set_cursor_position")
@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_move_to_valid_primary_screen(mock_bounds, mock_set_pos, mock_wm):
    """move_to moves cursor to valid coordinates on primary monitor."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.move_to(x=500, y=300)

    assert res["success"] is True
    assert res["coordinates"] == {"x": 500, "y": 300}
    mock_set_pos.assert_called_once_with(500, 300)


@patch.object(MouseController, "_set_cursor_position")
@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(-1920, 0, 3840, 1080))
def test_move_to_negative_multi_monitor_coordinates(mock_bounds, mock_set_pos, mock_wm):
    """move_to accepts negative coordinates spanning secondary monitor to the left."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.move_to(x=-500, y=200)

    assert res["success"] is True
    assert res["coordinates"] == {"x": -500, "y": 200}
    mock_set_pos.assert_called_once_with(-500, 200)


@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_move_to_coordinates_out_of_bounds(mock_bounds, mock_wm):
    """move_to raises CoordinatesOutOfBoundsError when coordinates exceed virtual desktop."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(CoordinatesOutOfBoundsError, match="outside virtual screen bounds"):
        mc.move_to(x=2500, y=500)


# ==============================================================================
# 2. Window-Relative & Target Window Boundary Tests
# ==============================================================================

@patch.object(MouseController, "_set_cursor_position")
@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_move_to_window_relative_coordinates(mock_bounds, mock_set_pos, mock_wm):
    """move_to converts window-relative coordinates (50, 60) to screen coordinates (150, 160)."""
    mc = MouseController(window_manager=mock_wm)
    # Target window is at left=100, top=100, right=500, bottom=400 (400x300)
    res = mc.move_to(x=50, y=60, target_hwnd=2001, relative_to_window=True)

    assert res["success"] is True
    assert res["coordinates"] == {"x": 150, "y": 160}
    mock_set_pos.assert_called_once_with(150, 160)


@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_move_to_window_relative_exceeding_window_bounds(mock_bounds, mock_wm):
    """move_to raises CoordinatesOutsideWindowError when relative coordinates exceed window size."""
    mc = MouseController(window_manager=mock_wm)
    # Target window is 400x300; x=450 exceeds width
    with pytest.raises(CoordinatesOutsideWindowError, match="fall outside target window bounds"):
        mc.move_to(x=450, y=50, target_hwnd=2001, relative_to_window=True)


@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_move_to_screen_coords_outside_target_window(mock_bounds, mock_wm):
    """move_to raises CoordinatesOutsideWindowError when absolute coords lie outside target window."""
    mc = MouseController(window_manager=mock_wm)
    # Target window is at [100, 500), [100, 400); screen coord (50, 50) is outside
    with pytest.raises(CoordinatesOutsideWindowError, match="fall outside target window rectangle"):
        mc.move_to(x=50, y=50, target_hwnd=2001, relative_to_window=False)


def test_move_to_relative_without_hwnd_rejected(mock_wm):
    """relative_to_window=True without target_hwnd raises InvalidInputError."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="requires an explicit target_hwnd"):
        mc.move_to(x=50, y=50, target_hwnd=None, relative_to_window=True)


# ==============================================================================
# 3. Mouse Click & Double Click Tests
# ==============================================================================

@pytest.mark.parametrize("button_name", ["left", "right", "middle"])
@patch("backend.automation.mouse_controller.win32gui.GetForegroundWindow", return_value=2001)
@patch.object(MouseController, "_set_cursor_position")
@patch.object(MouseController, "_send_mouse_input")
@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_click_supported_buttons(mock_bounds, mock_send, mock_set_pos, mock_fg, button_name, mock_wm):
    """click dispatches valid down/up events for left, right, and middle buttons."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.click(x=200, y=200, target_hwnd=2001, button=button_name, clicks=1)

    assert res["success"] is True
    assert res["button"] == button_name
    assert res["clicks"] == 1
    # 1 click = 1 down event + 1 up event = 2 SendInput calls
    assert mock_send.call_count == 2


@patch("backend.automation.mouse_controller.win32gui.GetForegroundWindow", return_value=2001)
@patch.object(MouseController, "_set_cursor_position")
@patch.object(MouseController, "_send_mouse_input")
@patch.object(MouseController, "get_virtual_screen_bounds", return_value=(0, 0, 1920, 1080))
def test_double_click_success(mock_bounds, mock_send, mock_set_pos, mock_fg, mock_wm):
    """double_click dispatches 2 clicks (4 down/up events)."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.double_click(x=200, y=200, target_hwnd=2001, button="left")

    assert res["success"] is True
    assert res["action"] == "double_click"
    assert res["clicks"] == 2
    assert mock_send.call_count == 4


@pytest.mark.parametrize("invalid_button", ["wheel", "fourth", "xbutton", "side", ""])
def test_click_invalid_button_rejected(invalid_button, mock_wm):
    """click rejects unsupported mouse buttons with InvalidMouseButtonError."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(InvalidMouseButtonError, match="not supported"):
        mc.click(x=200, y=200, target_hwnd=2001, button=invalid_button)


@pytest.mark.parametrize("invalid_clicks", [0, 3, 5, -1])
def test_click_invalid_clicks_count_rejected(invalid_clicks, mock_wm):
    """click rejects click counts other than 1 or 2 with InvalidClickCountError."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(InvalidClickCountError, match="must be 1 or 2"):
        mc.click(x=200, y=200, target_hwnd=2001, clicks=invalid_clicks)


# ==============================================================================
# 4. Scroll Tests
# ==============================================================================

@patch("backend.automation.mouse_controller.win32gui.GetForegroundWindow", return_value=2001)
@patch.object(MouseController, "_send_mouse_input")
def test_scroll_vertical_success(mock_send, mock_fg, mock_wm):
    """scroll dispatches vertical wheel event with specified delta."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.scroll(amount=120, target_hwnd=2001, horizontal=False)

    assert res["success"] is True
    assert res["action"] == "scroll"
    assert res["amount"] == 120
    assert res["horizontal"] is False
    # Verifies SendInput called with MOUSEEVENTF_WHEEL (0x0800) and mouse_data=120
    mock_send.assert_called_once_with(0x0800, mouse_data=120)


@patch("backend.automation.mouse_controller.win32gui.GetForegroundWindow", return_value=2001)
@patch.object(MouseController, "_send_mouse_input")
def test_scroll_horizontal_success(mock_send, mock_fg, mock_wm):
    """scroll dispatches horizontal wheel event with specified delta."""
    mc = MouseController(window_manager=mock_wm)
    res = mc.scroll(amount=-120, target_hwnd=2001, horizontal=True)

    assert res["success"] is True
    assert res["horizontal"] is True
    # Verifies SendInput called with MOUSEEVENTF_HWHEEL (0x1000) and mouse_data=-120
    mock_send.assert_called_once_with(0x1000, mouse_data=-120)


def test_scroll_zero_amount_rejected(mock_wm):
    """scroll rejects amount=0 with InvalidInputError."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="non-zero integer"):
        mc.scroll(amount=0, target_hwnd=2001)


def test_scroll_out_of_bounds_amount_rejected(mock_wm):
    """scroll rejects amounts outside [-2000, 2000] with InvalidInputError."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="outside allowed range"):
        mc.scroll(amount=2500, target_hwnd=2001)


# ==============================================================================
# 5. Target Window State & Focus Tests
# ==============================================================================

def test_click_minimized_window_rejected(mock_wm):
    """click rejects minimized window with WindowStateError."""
    info_minimized = WindowInfo(
        hwnd=2001, title="Notepad", app_id="notepad", display_name="Notepad",
        pid=1234, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=True, is_maximized=False, is_responsive=True,
        rect={"left": 100, "top": 100, "right": 500, "bottom": 400},
    )
    mock_wm.get_window_info.return_value = info_minimized
    mc = MouseController(window_manager=mock_wm)

    with pytest.raises(WindowStateError, match="is minimized"):
        mc.click(x=200, y=200, target_hwnd=2001)


def test_click_unresponsive_window_rejected(mock_wm):
    """click rejects hung window with TargetWindowUnresponsiveError."""
    mock_wm.is_window_responsive.return_value = False
    mc = MouseController(window_manager=mock_wm)

    with pytest.raises(TargetWindowUnresponsiveError, match="unresponsive/hung"):
        mc.click(x=200, y=200, target_hwnd=2001)


def test_click_activation_restricted(mock_wm):
    """click raises ActivationRestrictedError when Windows refuses activation."""
    mock_wm.activate_window.return_value = False
    mc = MouseController(window_manager=mock_wm)

    with pytest.raises(ActivationRestrictedError, match="did not allow foreground activation"):
        mc.click(x=200, y=200, target_hwnd=2001)


@patch("backend.automation.mouse_controller.win32gui.GetForegroundWindow", return_value=9999)
def test_click_target_not_active(mock_fg, mock_wm):
    """click raises TargetNotActiveError when target fails to become foreground."""
    mc = MouseController(window_manager=mock_wm)
    with pytest.raises(TargetNotActiveError, match="not foreground"):
        mc.click(x=200, y=200, target_hwnd=2001)


# ==============================================================================
# 6. Tool Interface Tests
# ==============================================================================

def test_click_mouse_input_validation():
    """ClickMouseInput validates positive HWND, allowed buttons, and click count."""
    # Invalid button
    with pytest.raises(ValueError, match="not supported"):
        ClickMouseInput(x=10, y=10, hwnd=2001, button="invalid")

    # Invalid click count
    with pytest.raises(ValueError):
        ClickMouseInput(x=10, y=10, hwnd=2001, clicks=4)

    # Valid input
    valid = ClickMouseInput(x=50, y=50, hwnd=2001, button="RIGHT", clicks=2)
    assert valid.button == "right"
    assert valid.clicks == 2


def test_scroll_mouse_input_validation():
    """ScrollMouseInput rejects zero and out-of-bounds amounts."""
    with pytest.raises(ValueError, match="non-zero"):
        ScrollMouseInput(amount=0, hwnd=2001)

    with pytest.raises(ValueError):
        ScrollMouseInput(amount=3000, hwnd=2001)


@patch.object(MouseController, "move_to")
def test_move_mouse_tool_success_outcome(mock_move):
    """move_mouse_tool returns structured MouseActionResult with success=True."""
    mock_move.return_value = {"success": True, "coordinates": {"x": 300, "y": 400}}
    inp = MoveMouseInput(x=300, y=400)
    res = move_mouse_tool(inp)

    assert res.success is True
    assert res.action == "move"
    assert res.coordinates == {"x": 300, "y": 400}
    assert res.status == "success"


@patch.object(MouseController, "click")
def test_click_mouse_tool_bounds_error_outcome(mock_click):
    """click_mouse_tool maps CoordinatesOutsideWindowError to status='bounds_error'."""
    mock_click.side_effect = CoordinatesOutsideWindowError("Coordinates outside window")
    inp = ClickMouseInput(x=999, y=999, hwnd=2001)
    res = click_mouse_tool(inp)

    assert res.success is False
    assert res.status == "bounds_error"


@patch.object(MouseController, "scroll")
def test_scroll_mouse_tool_target_not_allowed_outcome(mock_scroll):
    """scroll_mouse_tool maps WindowNotAllowedError to status='target_not_allowed'."""
    mock_scroll.side_effect = WindowNotAllowedError("Window not allowed")
    inp = ScrollMouseInput(amount=120, hwnd=9999)
    res = scroll_mouse_tool(inp)

    assert res.success is False
    assert res.status == "target_not_allowed"


@patch.object(MouseController, "click")
def test_click_mouse_tool_uipi_restricted_outcome(mock_click):
    """click_mouse_tool maps UIPIRestrictedError to status='uipi_restricted'."""
    mock_click.side_effect = UIPIRestrictedError("UIPI elevation restriction")
    inp = ClickMouseInput(x=200, y=200, hwnd=2001)
    res = click_mouse_tool(inp)

    assert res.success is False
    assert res.status == "uipi_restricted"

