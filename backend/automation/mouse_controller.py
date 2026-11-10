"""
VoxPilot - Windows Mouse Automation Subsystem
Provides safe, allowlisted, coordinate-validated mouse interaction for Windows 11.
Supports full multi-monitor virtual screen bounds (including negative coordinates),
window-relative coordinate conversion, target window bounds enforcement, click count limits,
and safe scrolling.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import time
from typing import Optional, Dict, Tuple, Any

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    win32api = None  # type: ignore
    win32con = None  # type: ignore
    win32gui = None  # type: ignore

from backend.automation.window_manager import (
    WindowManager,
    WindowManagerError,
    WindowNotFoundError,
    WindowNotAllowedError,
)
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
    release_system_inputs,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Win32 Mouse Structures and Constants
# ==============================================================================

INPUT_MOUSE = 0

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


# Allowed mouse buttons and their down/up flag pairs
BUTTON_FLAGS: Dict[str, Tuple[int, int]] = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Operational bounds
MIN_SCROLL_AMOUNT = -2000
MAX_SCROLL_AMOUNT = 2000


# ==============================================================================
# Exceptions
# ==============================================================================

class MouseControllerError(Exception):
    """Base exception for mouse automation errors."""
    pass


class InvalidInputError(MouseControllerError):
    """Raised when parameters fail validation (types, values, nulls)."""
    pass


class CoordinatesOutOfBoundsError(MouseControllerError):
    """Raised when coordinates fall outside the Windows virtual desktop bounds."""
    pass


class CoordinatesOutsideWindowError(MouseControllerError):
    """Raised when coordinates fall outside the target window rectangle."""
    pass


class InvalidMouseButtonError(MouseControllerError):
    """Raised when an unsupported mouse button is requested."""
    pass


class InvalidClickCountError(MouseControllerError):
    """Raised when click count is not 1 or 2."""
    pass


class WindowStateError(MouseControllerError):
    """Raised when target window is minimized, hidden, or in an invalid state."""
    pass


class TargetNotActiveError(MouseControllerError):
    """Raised when target window cannot be brought to foreground before mouse action."""
    pass


class ActivationRestrictedError(MouseControllerError):
    """Raised when Windows restricts foreground activation for target window."""
    pass


class TargetWindowUnresponsiveError(MouseControllerError):
    """Raised when target window is unresponsive or hung."""
    pass


class InputFailedError(MouseControllerError):
    """Raised when SendInput or SetCursorPos fails."""
    pass


class UIPIRestrictedError(MouseControllerError):
    """Raised when Windows UIPI blocks mouse interaction with an elevated window."""
    pass


# ==============================================================================
# Mouse Controller Class
# ==============================================================================

class MouseController:
    """Safe Windows mouse automation controller.

    Supports multi-monitor virtual screen bounds (including negative coordinates),
    window-relative coordinates, target window bounds verification, click limits,
    and safe scrolling.
    """

    def __init__(self, window_manager: Optional[WindowManager] = None) -> None:
        self._window_manager = window_manager if window_manager is not None else WindowManager()

    def get_virtual_screen_bounds(self) -> Tuple[int, int, int, int]:
        """Query the full bounding rectangle of the Windows virtual desktop.

        Handles multi-monitor configurations where secondary displays have negative coordinates.

        Returns:
            Tuple[int, int, int, int]: (left, top, width, height)
        """
        if win32api is not None and win32con is not None:
            try:
                left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
                top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
                width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
                height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
                if width > 0 and height > 0:
                    return (left, top, width, height)
            except Exception as err:
                logger.debug(f"win32api.GetSystemMetrics failed: {err}")

        # Fallback via user32 if pywin32 is unmocked or unavailable
        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            try:
                SM_XVIRTUALSCREEN = 76
                SM_YVIRTUALSCREEN = 77
                SM_CXVIRTUALSCREEN = 78
                SM_CYVIRTUALSCREEN = 79
                left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
                top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
                width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
                height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
                if width > 0 and height > 0:
                    return (left, top, width, height)
            except Exception as err:
                logger.debug(f"ctypes GetSystemMetrics failed: {err}")

        # Standard primary display fallback
        return (0, 0, 1920, 1080)

    def _send_mouse_input(self, dw_flags: int, mouse_data: int = 0) -> None:
        """Dispatch a single mouse event via Win32 SendInput."""
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            raise InputFailedError("ctypes.windll.user32 is not available on this platform.")

        mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=mouse_data,
            dwFlags=dw_flags,
            time=0,
            dwExtraInfo=0,
        )
        inp = INPUT(type=INPUT_MOUSE, u=INPUT_UNION(mi=mi))
        arr = (INPUT * 1)(inp)

        sent = ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))
        if sent == 0:
            err = ctypes.GetLastError()
            if err == 5:  # ERROR_ACCESS_DENIED
                raise UIPIRestrictedError(
                    "SendInput blocked by Windows UIPI: target application is running with higher elevation."
                )
            raise InputFailedError(f"SendInput mouse event failed with Win32 error code {err}.")

    def _set_cursor_position(self, x: int, y: int) -> None:
        """Position the cursor at absolute screen coordinates (x, y)."""
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            raise InputFailedError("ctypes.windll.user32 is not available on this platform.")

        res = ctypes.windll.user32.SetCursorPos(x, y)
        if res == 0:
            err = ctypes.GetLastError()
            if err == 5:
                raise UIPIRestrictedError(
                    "SetCursorPos blocked by Windows UIPI: cannot control cursor over elevated window."
                )
            raise InputFailedError(f"SetCursorPos failed with Win32 error code {err}.")

    def _validate_and_translate_coordinates(
        self,
        x: int,
        y: int,
        target_hwnd: Optional[int] = None,
        relative_to_window: bool = False,
    ) -> Tuple[int, int]:
        """Validate coordinates against virtual desktop and target window bounds.

        Args:
            x: X-coordinate (screen or window-relative).
            y: Y-coordinate (screen or window-relative).
            target_hwnd: Optional target window HWND.
            relative_to_window: Whether (x, y) are offsets from window top-left.

        Returns:
            Tuple[int, int]: Absolute screen coordinates (screen_x, screen_y).

        Raises:
            InvalidInputError: If relative_to_window is True without target_hwnd.
            WindowStateError: If target window is minimized.
            CoordinatesOutsideWindowError: If coordinates exceed window boundaries.
            CoordinatesOutOfBoundsError: If coordinates fall outside the virtual desktop.
        """
        if not isinstance(x, int) or not isinstance(y, int):
            raise InvalidInputError(f"Coordinates must be integers, got: x={x!r}, y={y!r}")

        screen_x = x
        screen_y = y

        # Window-scoped validation and relative coordinate conversion
        if target_hwnd is not None:
            info = self._window_manager.get_window_info(target_hwnd)

            if info.is_minimized:
                raise WindowStateError(f"Target window {target_hwnd} is minimized; cannot target coordinates.")

            rect = info.rect
            w_left = rect["left"]
            w_top = rect["top"]
            w_right = rect["right"]
            w_bottom = rect["bottom"]
            w_width = w_right - w_left
            w_height = w_bottom - w_top

            if relative_to_window:
                if x < 0 or x >= w_width or y < 0 or y >= w_height:
                    raise CoordinatesOutsideWindowError(
                        f"Relative coordinates ({x}, {y}) fall outside target window bounds ({w_width}x{w_height})."
                    )
                screen_x = w_left + x
                screen_y = w_top + y
            else:
                if not (w_left <= x < w_right and w_top <= y < w_bottom):
                    raise CoordinatesOutsideWindowError(
                        f"Screen coordinates ({x}, {y}) fall outside target window rectangle "
                        f"({w_left}, {w_top}, {w_right}, {w_bottom})."
                    )
        else:
            if relative_to_window:
                raise InvalidInputError("relative_to_window=True requires an explicit target_hwnd.")

        # Multi-monitor virtual desktop bounds check
        vx, vy, vw, vh = self.get_virtual_screen_bounds()
        if not (vx <= screen_x < vx + vw and vy <= screen_y < vy + vh):
            raise CoordinatesOutOfBoundsError(
                f"Coordinates ({screen_x}, {screen_y}) lie outside virtual screen bounds "
                f"(x: [{vx}, {vx + vw}), y: [{vy}, {vy + vh}))."
            )

        return (screen_x, screen_y)

    def _prepare_target_window_for_action(self, target_hwnd: int) -> None:
        """Verify window ownership, responsiveness, activation, and foreground focus."""
        if not isinstance(target_hwnd, int) or target_hwnd <= 0:
            raise InvalidInputError(f"Target HWND must be a positive integer, got: {target_hwnd!r}")

        # 1. Ownership validation
        self._window_manager.validate_window_ownership(target_hwnd)

        # 2. Responsiveness check
        if not self._window_manager.is_window_responsive(target_hwnd):
            raise TargetWindowUnresponsiveError(
                f"Target window {target_hwnd} is unresponsive/hung and cannot receive mouse interaction."
            )

        # 3. Minimized state check
        info = self._window_manager.get_window_info(target_hwnd)
        if info.is_minimized:
            raise WindowStateError(f"Target window {target_hwnd} is minimized; cannot interact with minimized window.")

        # 4. Safe activation
        try:
            activated = self._window_manager.activate_window(target_hwnd)
        except Exception as err:
            raise ActivationRestrictedError(f"Failed to activate target window {target_hwnd}: {err}") from err

        if not activated:
            raise ActivationRestrictedError(
                f"Windows did not allow foreground activation for target window {target_hwnd}."
            )

        # 4. Foreground verification
        if win32gui is not None:
            current_fg = win32gui.GetForegroundWindow()
            if current_fg != target_hwnd:
                raise TargetNotActiveError(
                    f"Target window {target_hwnd} is not foreground (current foreground: {current_fg})."
                )

    def release_all_buttons(self) -> None:
        """Release all mouse buttons (left, right, middle) to guarantee safe physical mouse state."""
        release_system_inputs()

    def move_to(
        self,
        x: int,
        y: int,
        target_hwnd: Optional[int] = None,
        relative_to_window: bool = False,
    ) -> Dict[str, Any]:
        """Move the mouse pointer to the specified coordinates.

        Args:
            x: Target X-coordinate.
            y: Target Y-coordinate.
            target_hwnd: Optional HWND to scope coordinates and validate window bounds.
            relative_to_window: If True, treat (x, y) as offset from window top-left.

        Returns:
            Dict[str, Any]: Execution result with final screen coordinates.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()

        screen_x, screen_y = self._validate_and_translate_coordinates(
            x=x,
            y=y,
            target_hwnd=target_hwnd,
            relative_to_window=relative_to_window,
        )

        logger.info(f"Moving mouse pointer to screen coordinates ({screen_x}, {screen_y}).")
        self._set_cursor_position(screen_x, screen_y)

        return {
            "success": True,
            "action": "move",
            "hwnd": target_hwnd,
            "coordinates": {"x": screen_x, "y": screen_y},
            "status": "success",
        }

    def click(
        self,
        x: int,
        y: int,
        target_hwnd: int,
        button: str = "left",
        clicks: int = 1,
        relative_to_window: bool = False,
    ) -> Dict[str, Any]:
        """Perform a single or double click in the target window.

        Args:
            x: Click X-coordinate.
            y: Click Y-coordinate.
            target_hwnd: Validated target window handle (mandatory).
            button: 'left', 'right', or 'middle' (default 'left').
            clicks: Number of clicks: 1 or 2 (default 1).
            relative_to_window: If True, (x, y) are offsets from window top-left.

        Returns:
            Dict[str, Any]: Execution result.

        Raises:
            InvalidMouseButtonError: If button is not supported.
            InvalidClickCountError: If clicks not in (1, 2).
            CoordinatesOutsideWindowError: If coordinates are outside target window.
            TargetNotActiveError: If target cannot be made foreground.
        """
        norm_button = button.strip().lower()
        if norm_button not in BUTTON_FLAGS:
            raise InvalidMouseButtonError(
                f"Mouse button '{button}' is not supported. Supported: {list(BUTTON_FLAGS.keys())}."
            )

        if clicks not in (1, 2):
            raise InvalidClickCountError(f"Click count must be 1 or 2, got: {clicks!r}")

        # Emergency stop pre-execution check
        get_shared_emergency_stop_manager().check_emergency_stop()

        # Prepare and activate window first
        self._prepare_target_window_for_action(target_hwnd)

        # Validate coordinates and convert to absolute screen coordinates
        screen_x, screen_y = self._validate_and_translate_coordinates(
            x=x,
            y=y,
            target_hwnd=target_hwnd,
            relative_to_window=relative_to_window,
        )

        # Position cursor at click location
        self._set_cursor_position(screen_x, screen_y)
        time.sleep(0.02)

        # Foreground re-verification immediately before click
        if win32gui is not None:
            if win32gui.GetForegroundWindow() != target_hwnd:
                raise TargetNotActiveError(
                    f"Target window {target_hwnd} lost focus before click could be dispatched."
                )

        down_flag, up_flag = BUTTON_FLAGS[norm_button]
        logger.info(
            f"Clicking {norm_button} ({clicks} clicks) at ({screen_x}, {screen_y}) in HWND {target_hwnd}."
        )

        for c_idx in range(clicks):
            manager = get_shared_emergency_stop_manager()
            if manager.is_emergency_stop_active():
                self.release_all_buttons()
                raise EmergencyStopActiveError(
                    f"Mouse click aborted: emergency stop is active ({manager.trigger_reason}).",
                    reason=manager.trigger_reason,
                )
            self._send_mouse_input(down_flag)
            time.sleep(0.01)
            self._send_mouse_input(up_flag)
            if clicks > 1 and c_idx == 0:
                time.sleep(0.05)

        return {
            "success": True,
            "action": "click" if clicks == 1 else "double_click",
            "button": norm_button,
            "clicks": clicks,
            "hwnd": target_hwnd,
            "coordinates": {"x": screen_x, "y": screen_y},
            "status": "success",
        }

    def double_click(
        self,
        x: int,
        y: int,
        target_hwnd: int,
        button: str = "left",
        relative_to_window: bool = False,
    ) -> Dict[str, Any]:
        """Convenience method for double-clicking at the specified coordinates."""
        return self.click(
            x=x,
            y=y,
            target_hwnd=target_hwnd,
            button=button,
            clicks=2,
            relative_to_window=relative_to_window,
        )

    def scroll(
        self,
        amount: int,
        target_hwnd: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        horizontal: bool = False,
        relative_to_window: bool = False,
    ) -> Dict[str, Any]:
        """Scroll the mouse wheel in the target window.

        Args:
            amount: Scroll delta in wheel units (positive = up/right, negative = down/left).
            target_hwnd: Validated target window handle.
            x: Optional X-coordinate to position pointer before scrolling.
            y: Optional Y-coordinate to position pointer before scrolling.
            horizontal: True for horizontal scrolling (HWHEEL), False for vertical (WHEEL).
            relative_to_window: If True, (x, y) are offsets from window top-left.

        Returns:
            Dict[str, Any]: Execution result.
        """
        if not isinstance(amount, int) or amount == 0:
            raise InvalidInputError(f"Scroll amount must be a non-zero integer, got: {amount!r}")

        if not (MIN_SCROLL_AMOUNT <= amount <= MAX_SCROLL_AMOUNT):
            raise InvalidInputError(
                f"Scroll amount {amount} outside allowed range [{MIN_SCROLL_AMOUNT}, {MAX_SCROLL_AMOUNT}]."
            )

        # Emergency stop pre-execution check
        get_shared_emergency_stop_manager().check_emergency_stop()

        # Prepare and activate window
        self._prepare_target_window_for_action(target_hwnd)

        screen_coords: Optional[Dict[str, int]] = None
        if x is not None and y is not None:
            screen_x, screen_y = self._validate_and_translate_coordinates(
                x=x,
                y=y,
                target_hwnd=target_hwnd,
                relative_to_window=relative_to_window,
            )
            self._set_cursor_position(screen_x, screen_y)
            screen_coords = {"x": screen_x, "y": screen_y}
            time.sleep(0.02)

        # Foreground re-verification before scroll
        if win32gui is not None:
            if win32gui.GetForegroundWindow() != target_hwnd:
                raise TargetNotActiveError(
                    f"Target window {target_hwnd} lost focus before scroll could be dispatched."
                )

        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        logger.info(
            f"Scrolling {'horizontal' if horizontal else 'vertical'} by {amount} units in HWND {target_hwnd}."
        )

        self._send_mouse_input(flag, mouse_data=amount)

        return {
            "success": True,
            "action": "scroll",
            "amount": amount,
            "horizontal": horizontal,
            "hwnd": target_hwnd,
            "coordinates": screen_coords,
            "status": "success",
        }
