"""
VoxPilot - Keyboard Automation Subsystem Unit Tests
Tests text typing, Unicode surrogate pairs, text length & interval validation,
approved/prohibited keys and shortcuts, modifier cleanup on failure, focus loss detection,
SendInput error handling, UIPI detection, and tool schemas.
"""

import time
import pytest
from unittest.mock import MagicMock, patch, call

from backend.automation.window_manager import (
    WindowInfo,
    WindowManager,
    WindowNotFoundError,
    WindowNotAllowedError,
)
from backend.automation.keyboard_controller import (
    KeyboardController,
    KeyboardControllerError,
    InvalidInputError,
    InvalidKeyError,
    InvalidShortcutError,
    ProhibitedShortcutError,
    TargetNotActiveError,
    ActivationRestrictedError,
    TargetWindowUnresponsiveError,
    FocusLostError,
    InputFailedError,
    UIPIRestrictedError,
    APPROVED_KEYS,
    APPROVED_SHORTCUTS,
    PROHIBITED_SHORTCUTS,
    MAX_TEXT_LENGTH,
    MIN_TYPING_INTERVAL,
    MAX_TYPING_INTERVAL,
)
from backend.tools.keyboard import (
    TypeTextInput,
    PressKeyInput,
    SendShortcutInput,
    KeyboardActionResult,
    type_text_tool,
    press_key_tool,
    send_shortcut_tool,
)


@pytest.fixture
def mock_wm():
    """Create a mock WindowManager where HWND 1001 is a responsive, approved Notepad window."""
    wm = MagicMock(spec=WindowManager)
    wm.validate_window_ownership.return_value = (
        "notepad",
        "Notepad",
        1234,
        "notepad.exe",
        r"C:\Windows\System32\notepad.exe",
    )
    wm.is_window_responsive.return_value = True
    wm.activate_window.return_value = True
    return wm


# ==============================================================================
# 1. Text Typing Tests (Unicode, Bounds, Intervals)
# ==============================================================================

@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=1001)
@patch.object(KeyboardController, "_send_inputs")
def test_type_text_valid_ascii(mock_send, mock_fg, mock_wm):
    """type_text successfully types ASCII characters via SendInput."""
    mock_send.side_effect = lambda inputs: len(inputs)

    kc = KeyboardController(window_manager=mock_wm)
    res = kc.type_text("Hello World!", target_hwnd=1001, interval=0.01)

    assert res["success"] is True
    assert res["characters_typed"] == 12
    assert res["status"] == "success"
    # Each char produces down and up events (2 events per char)
    assert mock_send.call_count == 12


@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=1001)
@patch.object(KeyboardController, "_send_inputs")
def test_type_text_unicode_and_surrogate_pairs(mock_send, mock_fg, mock_wm):
    """type_text correctly handles Unicode characters outside BMP (emojis) via UTF-16 surrogates."""
    mock_send.side_effect = lambda inputs: len(inputs)

    kc = KeyboardController(window_manager=mock_wm)
    # Emoji \U0001F600 has 2 UTF-16 surrogate code units
    emoji_text = "Hi \U0001F600!"
    res = kc.type_text(emoji_text, target_hwnd=1001, interval=0.01)

    assert res["success"] is True
    assert res["characters_typed"] == len(emoji_text)

    # Inspect the inputs passed for the emoji character
    # The emoji call is the 4th call (index 3: 'H', 'i', ' ', '\U0001F600')
    emoji_call_inputs = mock_send.call_args_list[3][0][0]
    # Should produce 4 inputs: high surrogate (down, up), low surrogate (down, up)
    assert len(emoji_call_inputs) == 4


def test_type_text_empty_rejected(mock_wm):
    """type_text rejects empty text with InvalidInputError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="non-empty string"):
        kc.type_text("", target_hwnd=1001)


def test_type_text_oversized_rejected(mock_wm):
    """type_text rejects text exceeding MAX_TEXT_LENGTH (500 characters)."""
    kc = KeyboardController(window_manager=mock_wm)
    oversized = "A" * (MAX_TEXT_LENGTH + 1)
    with pytest.raises(InvalidInputError, match="maximum allowed length"):
        kc.type_text(oversized, target_hwnd=1001)


def test_type_text_interval_bounds(mock_wm):
    """type_text enforces typing interval limits (0.005s to 0.05s)."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="allowed range"):
        kc.type_text("Test", target_hwnd=1001, interval=0.001)

    with pytest.raises(InvalidInputError, match="allowed range"):
        kc.type_text("Test", target_hwnd=1001, interval=0.1)


# ==============================================================================
# 2. Focus Tracking & Chunked Typing Tests
# ==============================================================================

@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow")
@patch.object(KeyboardController, "_send_inputs")
def test_type_text_focus_lost_stops_typing(mock_send, mock_fg, mock_wm):
    """type_text verifies foreground before each chunk and stops immediately if focus shifts."""
    mock_send.side_effect = lambda inputs: len(inputs)
    # Target is 1001. First check passes, second chunk check (after 10 chars) returns 9999 (focus lost!)
    mock_fg.side_effect = [1001, 1001, 9999]

    kc = KeyboardController(window_manager=mock_wm)
    text_25_chars = "1234567890123456789012345"

    with pytest.raises(FocusLostError) as exc_info:
        kc.type_text(text_25_chars, target_hwnd=1001, interval=0.005)

    assert exc_info.value.characters_typed == 10
    assert "no longer foreground" in str(exc_info.value)
    # Only 10 characters should have been dispatched
    assert mock_send.call_count == 10


# ==============================================================================
# 3. Approved Key & Disallowed Key Tests
# ==============================================================================

@pytest.mark.parametrize("key_name", list(APPROVED_KEYS.keys()))
@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=1001)
@patch.object(KeyboardController, "_send_inputs", return_value=2)
def test_press_key_all_approved_keys_succeed(mock_send, mock_fg, key_name, mock_wm):
    """All approved keys in APPROVED_KEYS are accepted and dispatched."""
    kc = KeyboardController(window_manager=mock_wm)
    res = kc.press_key(key_name, target_hwnd=1001)

    assert res["success"] is True
    assert res["key"] == key_name
    assert mock_send.call_count == 1
    # Inputs list should contain down and up events
    inputs = mock_send.call_args[0][0]
    assert len(inputs) == 2


@pytest.mark.parametrize("invalid_key", ["f4", "f12", "win", "windows", "ctrl", "alt", "capslock", "prtsc"])
def test_press_key_invalid_keys_rejected(invalid_key, mock_wm):
    """Unapproved keys (e.g. F-keys, Windows keys, modifiers) are rejected with InvalidKeyError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(InvalidKeyError, match="not in approved keys allowlist"):
        kc.press_key(invalid_key, target_hwnd=1001)


# ==============================================================================
# 4. Approved & Prohibited Shortcuts Tests
# ==============================================================================

@pytest.mark.parametrize("shortcut", list(APPROVED_SHORTCUTS.keys()))
@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=1001)
@patch.object(KeyboardController, "_send_inputs", return_value=1)
def test_send_shortcut_all_approved_shortcuts_succeed(mock_send, mock_fg, shortcut, mock_wm):
    """All approved shortcuts in APPROVED_SHORTCUTS succeed and release modifiers."""
    # SendInput returns 1 for modifier, 2 for key down/up
    def send_effect(inputs):
        return len(inputs)
    mock_send.side_effect = send_effect

    kc = KeyboardController(window_manager=mock_wm)
    res = kc.send_shortcut(shortcut, target_hwnd=1001)

    assert res["success"] is True
    assert res["shortcut"] == shortcut


@pytest.mark.parametrize("prohibited", list(PROHIBITED_SHORTCUTS))
def test_send_shortcut_prohibited_shortcuts_rejected(prohibited, mock_wm):
    """Prohibited shortcuts (Alt+F4, Win+R, Ctrl+Alt+Del, etc.) are rejected with ProhibitedShortcutError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(ProhibitedShortcutError, match="explicitly prohibited"):
        kc.send_shortcut(prohibited, target_hwnd=1001)


@pytest.mark.parametrize("unapproved", ["ctrl+p", "ctrl+o", "alt+tab", "ctrl+shift+n"])
def test_send_shortcut_unapproved_shortcuts_rejected(unapproved, mock_wm):
    """Shortcuts not in the allowlist are rejected with InvalidShortcutError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(InvalidShortcutError, match="not in approved shortcuts catalog"):
        kc.send_shortcut(unapproved, target_hwnd=1001)


@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=1001)
@patch.object(KeyboardController, "_send_inputs")
def test_send_shortcut_modifiers_cleaned_up_on_failure(mock_send, mock_fg, mock_wm):
    """When primary key press fails, pressed modifiers are unconditionally released in finally block."""
    calls = []
    def send_tracker(inputs):
        for inp in inputs:
            vk = inp.u.ki.wVk
            flags = inp.u.ki.dwFlags
            calls.append((vk, flags))
        # Fail when trying to press primary key
        if len(calls) > 1 and calls[-1][0] == ord("C"):
            raise InputFailedError("OS SendInput failure")
        return len(inputs)

    mock_send.side_effect = send_tracker
    kc = KeyboardController(window_manager=mock_wm)

    with pytest.raises(InputFailedError):
        kc.send_shortcut("Ctrl+C", target_hwnd=1001)

    # Verify that VK_CONTROL (0x11) received a release event (flags containing KEYEVENTF_KEYUP = 0x0002)
    release_events = [c for c in calls if c[0] == 0x11 and (c[1] & 0x0002)]
    assert len(release_events) >= 1, "Expected modifier VK_CONTROL to be released in finally block"


# ==============================================================================
# 5. Target Window Validation & Activation Restrictions
# ==============================================================================

def test_type_text_invalid_hwnd(mock_wm):
    """Negative or zero HWND raises InvalidInputError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(InvalidInputError, match="positive integer"):
        kc.type_text("Hello", target_hwnd=-5)


def test_type_text_unapproved_window(mock_wm):
    """Unapproved target window raises WindowNotAllowedError."""
    mock_wm.validate_window_ownership.side_effect = WindowNotAllowedError("Unapproved app")
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(WindowNotAllowedError):
        kc.type_text("Hello", target_hwnd=9999)


def test_type_text_hung_window(mock_wm):
    """Hung/unresponsive target window raises TargetWindowUnresponsiveError."""
    mock_wm.is_window_responsive.return_value = False
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(TargetWindowUnresponsiveError, match="unresponsive/hung"):
        kc.type_text("Hello", target_hwnd=1001)


def test_type_text_activation_restricted(mock_wm):
    """When Windows restricts activation, raises ActivationRestrictedError."""
    mock_wm.activate_window.return_value = False
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(ActivationRestrictedError, match="did not allow foreground activation"):
        kc.type_text("Hello", target_hwnd=1001)


@patch("backend.automation.keyboard_controller.win32gui.GetForegroundWindow", return_value=8888)
def test_type_text_target_not_active(mock_fg, mock_wm):
    """When target window fails to become foreground, raises TargetNotActiveError."""
    kc = KeyboardController(window_manager=mock_wm)
    with pytest.raises(TargetNotActiveError, match="not the foreground window"):
        kc.type_text("Hello", target_hwnd=1001)


# ==============================================================================
# 6. Tool Interface Tests
# ==============================================================================

def test_type_text_tool_schema_validation():
    """TypeTextInput schema rejects empty strings and oversized inputs."""
    with pytest.raises(ValueError):
        TypeTextInput(hwnd=1001, text="")

    with pytest.raises(ValueError):
        TypeTextInput(hwnd=1001, text="A" * 501)

    with pytest.raises(ValueError):
        TypeTextInput(hwnd=-1, text="Valid")


def test_press_key_tool_schema_validation():
    """PressKeyInput schema rejects unapproved key names."""
    with pytest.raises(ValueError, match="not an approved key"):
        PressKeyInput(hwnd=1001, key="f5")

    valid = PressKeyInput(hwnd=1001, key="ENTER")
    assert valid.key == "enter"


def test_send_shortcut_tool_schema_validation():
    """SendShortcutInput schema rejects prohibited and unapproved shortcuts."""
    with pytest.raises(ValueError, match="prohibited"):
        SendShortcutInput(hwnd=1001, shortcut="alt+f4")

    with pytest.raises(ValueError, match="not in approved shortcuts"):
        SendShortcutInput(hwnd=1001, shortcut="ctrl+p")

    valid = SendShortcutInput(hwnd=1001, shortcut="Ctrl + C")
    assert valid.shortcut == "ctrl+c"


@patch.object(KeyboardController, "type_text")
def test_type_text_tool_success_outcome(mock_type):
    """type_text_tool returns structured KeyboardActionResult with success=True."""
    mock_type.return_value = {"success": True, "characters_typed": 10}
    inp = TypeTextInput(hwnd=1001, text="Hello 1234")
    res = type_text_tool(inp)

    assert res.success is True
    assert res.action == "type_text"
    assert res.status == "success"
    assert res.characters_typed == 10


@patch.object(KeyboardController, "type_text")
def test_type_text_tool_focus_lost_outcome(mock_type):
    """type_text_tool maps FocusLostError to status='focus_lost'."""
    mock_type.side_effect = FocusLostError("Focus lost mid-typing", characters_typed=5)
    inp = TypeTextInput(hwnd=1001, text="Hello 1234")
    res = type_text_tool(inp)

    assert res.success is False
    assert res.status == "focus_lost"
    assert res.characters_typed == 5


@patch.object(KeyboardController, "press_key")
def test_press_key_tool_target_not_allowed_outcome(mock_press):
    """press_key_tool maps WindowNotAllowedError to status='target_not_allowed'."""
    mock_press.side_effect = WindowNotAllowedError("Window not allowed")
    inp = PressKeyInput(hwnd=1001, key="enter")
    res = press_key_tool(inp)

    assert res.success is False
    assert res.status == "target_not_allowed"


@patch.object(KeyboardController, "send_shortcut")
def test_send_shortcut_tool_uipi_restricted_outcome(mock_sc):
    """send_shortcut_tool maps UIPIRestrictedError to status='uipi_restricted'."""
    mock_sc.side_effect = UIPIRestrictedError("UIPI elevation restriction")
    inp = SendShortcutInput(hwnd=1001, shortcut="ctrl+c")
    res = send_shortcut_tool(inp)

    assert res.success is False
    assert res.status == "uipi_restricted"

