"""
VoxPilot - Windows Keyboard Automation Subsystem
Provides safe, allowlisted, window-scoped keyboard interaction for Windows 11.
Enforces strict allowlisting, text bounds, chunked Unicode typing, focus verification,
and modifier cleanup on exceptions.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Set, Tuple

try:
    import win32gui
    import win32con
    import win32process
except ImportError:
    win32gui = None  # type: ignore
    win32con = None  # type: ignore
    win32process = None  # type: ignore

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
# Win32 SendInput Structures and Constants
# ==============================================================================

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
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
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


# ==============================================================================
# Allowlist Constants & Security Definitions
# ==============================================================================

# Virtual-Key codes for approved individual keys
APPROVED_KEYS: Dict[str, int] = {
    "enter": 0x0D,      # VK_RETURN
    "tab": 0x09,        # VK_TAB
    "space": 0x20,      # VK_SPACE
    "backspace": 0x08,  # VK_BACK
    "delete": 0x2E,     # VK_DELETE
    "up": 0x26,         # VK_UP
    "down": 0x28,       # VK_DOWN
    "left": 0x25,       # VK_LEFT
    "right": 0x27,      # VK_RIGHT
    "home": 0x24,       # VK_HOME
    "end": 0x23,        # VK_END
    "pageup": 0x21,     # VK_PRIOR
    "pagedown": 0x22,   # VK_NEXT
    "escape": 0x1B,     # VK_ESCAPE
}

# Extended key flags for navigation keys
EXTENDED_VKS: Set[int] = {
    0x2E,  # VK_DELETE
    0x26,  # VK_UP
    0x28,  # VK_DOWN
    0x25,  # VK_LEFT
    0x27,  # VK_RIGHT
    0x24,  # VK_HOME
    0x23,  # VK_END
    0x21,  # VK_PRIOR
    0x22,  # VK_NEXT
}

# Modifiers
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt

# Approved shortcut catalog: normalized string -> (modifiers_list, target_vk)
APPROVED_SHORTCUTS: Dict[str, Tuple[List[int], int]] = {
    "ctrl+c": ([VK_CONTROL], ord("C")),
    "ctrl+v": ([VK_CONTROL], ord("V")),
    "ctrl+x": ([VK_CONTROL], ord("X")),
    "ctrl+a": ([VK_CONTROL], ord("A")),
    "ctrl+z": ([VK_CONTROL], ord("Z")),
    "ctrl+y": ([VK_CONTROL], ord("Y")),
    "ctrl+s": ([VK_CONTROL], ord("S")),
    "ctrl+f": ([VK_CONTROL], ord("F")),
    "ctrl+left": ([VK_CONTROL], 0x25),
    "ctrl+right": ([VK_CONTROL], 0x27),
    "ctrl+home": ([VK_CONTROL], 0x24),
    "ctrl+end": ([VK_CONTROL], 0x23),
    "shift+left": ([VK_SHIFT], 0x25),
    "shift+right": ([VK_SHIFT], 0x27),
    "shift+up": ([VK_SHIFT], 0x26),
    "shift+down": ([VK_SHIFT], 0x28),
}

# Explicitly prohibited shortcuts (destructive or security bypass vectors)
PROHIBITED_SHORTCUTS: Set[str] = {
    "alt+f4",
    "ctrl+w",
    "win+r",
    "win+x",
    "win+d",
    "win+m",
    "ctrl+alt+del",
    "ctrl+alt+delete",
    "ctrl+shift+escape",
    "ctrl+shift+esc",
}

# Operational bounds
MAX_TEXT_LENGTH = 500
MIN_TYPING_INTERVAL = 0.005
MAX_TYPING_INTERVAL = 0.05
DEFAULT_TYPING_INTERVAL = 0.01
CHUNK_SIZE = 10


# ==============================================================================
# Exceptions
# ==============================================================================

class KeyboardControllerError(Exception):
    """Base exception for keyboard automation errors."""
    pass


class InvalidInputError(KeyboardControllerError):
    """Raised when parameters fail validation (length, interval, types)."""
    pass


class InvalidKeyError(KeyboardControllerError):
    """Raised when a requested key is not in the approved key allowlist."""
    pass


class InvalidShortcutError(KeyboardControllerError):
    """Raised when a requested shortcut is not in the approved shortcut catalog."""
    pass


class ProhibitedShortcutError(KeyboardControllerError):
    """Raised when a requested shortcut is explicitly prohibited for security."""
    pass


class TargetNotActiveError(KeyboardControllerError):
    """Raised when the target window is not active or foreground before/during input."""
    pass


class ActivationRestrictedError(KeyboardControllerError):
    """Raised when Windows restricts foreground activation of the target window."""
    pass


class TargetWindowUnresponsiveError(KeyboardControllerError):
    """Raised when the target window is hung or unresponsive to message loop."""
    pass


class FocusLostError(KeyboardControllerError):
    """Raised when the target window loses foreground focus during chunked typing."""
    def __init__(self, message: str, characters_typed: int = 0):
        super().__init__(message)
        self.characters_typed = characters_typed


class InputFailedError(KeyboardControllerError):
    """Raised when Win32 SendInput fails to dispatch all events."""
    pass


class UIPIRestrictedError(KeyboardControllerError):
    """Raised when Windows UIPI (User Interface Privilege Isolation) blocks input to an elevated window."""
    pass


# ==============================================================================
# Keyboard Controller Class
# ==============================================================================

class KeyboardController:
    """Safe Windows keyboard automation controller.

    Enforces target window validation, responsiveness checks, standard activation,
    chunked Unicode typing via SendInput, focus tracking, and modifier cleanup.
    """

    def __init__(self, window_manager: Optional[WindowManager] = None) -> None:
        self._window_manager = window_manager if window_manager is not None else WindowManager()

    def _send_inputs(self, inputs: List[INPUT]) -> int:
        """Low-level wrapper around Win32 user32.SendInput with error inspection."""
        if not inputs:
            return 0

        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            raise InputFailedError("ctypes.windll.user32 is not available on this platform.")

        arr = (INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

        if sent == 0 and len(inputs) > 0:
            err = ctypes.GetLastError()
            if err == 5:  # ERROR_ACCESS_DENIED
                raise UIPIRestrictedError(
                    "SendInput blocked by Windows UIPI: target application is running with higher elevation."
                )
            raise InputFailedError(f"SendInput failed with Win32 error code {err}.")

        return int(sent)

    def _create_unicode_inputs(self, char: str) -> List[INPUT]:
        """Convert a character into UTF-16 code units and generate KEYEVENTF_UNICODE events.

        Correctly handles UTF-16 surrogate pairs for characters outside BMP (e.g. emojis).
        """
        utf16_bytes = char.encode("utf-16le")
        words = [int.from_bytes(utf16_bytes[i:i + 2], "little") for i in range(0, len(utf16_bytes), 2)]
        events: List[INPUT] = []

        for w in words:
            # Key down
            ki_down = KEYBDINPUT(wVk=0, wScan=w, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
            events.append(INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=ki_down)))
            # Key up
            ki_up = KEYBDINPUT(wVk=0, wScan=w, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            events.append(INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=ki_up)))

        return events

    def _create_vk_input(self, vk: int, is_down: bool, extended: bool = False) -> INPUT:
        """Create a Win32 INPUT structure for a Virtual-Key code."""
        flags = 0
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY

        ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        return INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=ki))

    def release_all_modifiers(self) -> None:
        """Release all commonly held modifier keys immediately."""
        release_system_inputs()

    def _prepare_target_window(self, target_hwnd: int) -> None:
        """Validate target window ownership, responsiveness, activation, and foreground focus.

        Raises:
            InvalidInputError: If target_hwnd is non-positive or invalid.
            WindowNotFoundError: If target window does not exist.
            WindowNotAllowedError: If target window does not belong to an approved application.
            TargetWindowUnresponsiveError: If target window is hung.
            ActivationRestrictedError: If Windows refuses foreground transfer.
            TargetNotActiveError: If target window is not foreground after activation.
        """
        if not isinstance(target_hwnd, int) or target_hwnd <= 0:
            raise InvalidInputError(f"Target HWND must be a positive integer, got: {target_hwnd!r}")

        # 1. Ownership & Allowlist validation
        self._window_manager.validate_window_ownership(target_hwnd)

        # 2. Responsiveness check
        if not self._window_manager.is_window_responsive(target_hwnd):
            raise TargetWindowUnresponsiveError(
                f"Target window {target_hwnd} is unresponsive/hung and cannot receive input."
            )

        # 3. Safe activation
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
                    f"Target window {target_hwnd} is not the foreground window (current foreground: {current_fg})."
                )

    def type_text(
        self,
        text: str,
        target_hwnd: int,
        interval: float = DEFAULT_TYPING_INTERVAL,
    ) -> Dict[str, Any]:
        """Type Unicode text into the target window using chunked SendInput events.

        Args:
            text: Unicode string to type (max 500 characters).
            target_hwnd: Positive HWND of the validated target window.
            interval: Delay in seconds between characters (0.005 to 0.05 seconds).

        Returns:
            Dict[str, Any]: Execution outcome with characters_typed count.

        Raises:
            InvalidInputError: On invalid arguments or bounds violations.
            FocusLostError: If focus shifts away from target_hwnd mid-typing.
            InputFailedError: If SendInput fails to dispatch events.
        """
        if not isinstance(text, str) or len(text) == 0:
            raise InvalidInputError("Text to type must be a non-empty string.")

        if len(text) > MAX_TEXT_LENGTH:
            raise InvalidInputError(
                f"Text exceeds maximum allowed length of {MAX_TEXT_LENGTH} characters (got {len(text)})."
            )

        if not (MIN_TYPING_INTERVAL <= interval <= MAX_TYPING_INTERVAL):
            raise InvalidInputError(
                f"Typing interval {interval}s outside allowed range "
                f"[{MIN_TYPING_INTERVAL}s, {MAX_TYPING_INTERVAL}s]."
            )

        # Emergency stop pre-execution check
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            self.release_all_modifiers()
            manager.check_emergency_stop()

        # Prepare and activate window
        self._prepare_target_window(target_hwnd)

        characters_typed = 0
        total_chars = len(text)

        # Safe logging without exposing sensitive text content
        logger.info(
            f"Typing {total_chars} characters into target HWND {target_hwnd} with interval {interval}s."
        )

        # Chunked transmission with inter-chunk foreground verification & emergency stop check
        for chunk_start in range(0, total_chars, CHUNK_SIZE):
            # Check emergency stop between chunks
            manager = get_shared_emergency_stop_manager()
            if manager.is_emergency_stop_active():
                self.release_all_modifiers()
                raise EmergencyStopActiveError(
                    f"Keyboard typing aborted after {characters_typed} characters: emergency stop is active ({manager.trigger_reason}).",
                    reason=manager.trigger_reason,
                )

            chunk = text[chunk_start:chunk_start + CHUNK_SIZE]

            # Verify foreground focus before every chunk
            if win32gui is not None:
                current_fg = win32gui.GetForegroundWindow()
                if current_fg != target_hwnd:
                    raise FocusLostError(
                        f"Focus lost during typing after {characters_typed} characters: "
                        f"target HWND {target_hwnd} is no longer foreground (active: {current_fg}).",
                        characters_typed=characters_typed,
                    )

            for char in chunk:
                inputs = self._create_unicode_inputs(char)
                sent = self._send_inputs(inputs)
                if sent < len(inputs):
                    raise InputFailedError(
                        f"SendInput partial failure: dispatched {sent} of {len(inputs)} events."
                    )
                characters_typed += 1

                if interval > 0:
                    time.sleep(interval)

        return {
            "success": True,
            "action": "type_text",
            "hwnd": target_hwnd,
            "characters_typed": characters_typed,
            "status": "success",
        }

    def press_key(self, key: str, target_hwnd: int) -> Dict[str, Any]:
        """Press a single approved key in the target window.

        Args:
            key: Name of the key to press (case-insensitive, e.g. 'enter', 'tab').
            target_hwnd: Positive HWND of the validated target window.

        Returns:
            Dict[str, Any]: Execution outcome.

        Raises:
            InvalidKeyError: If key is not in the approved key allowlist.
            TargetNotActiveError: If target window is not foreground.
            InputFailedError: If SendInput fails.
        """
        if not isinstance(key, str) or not key.strip():
            raise InvalidKeyError(f"Key name must be a non-empty string, got: {key!r}")

        norm_key = key.strip().lower()
        if norm_key not in APPROVED_KEYS:
            raise InvalidKeyError(
                f"Key '{key}' is not in approved keys allowlist. "
                f"Approved keys: {sorted(list(APPROVED_KEYS.keys()))}."
            )

        vk = APPROVED_KEYS[norm_key]
        extended = vk in EXTENDED_VKS

        # Emergency stop pre-execution check
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            self.release_all_modifiers()
            manager.check_emergency_stop()

        # Prepare and activate window
        self._prepare_target_window(target_hwnd)

        logger.info(f"Pressing approved key '{norm_key}' (VK 0x{vk:02X}) in HWND {target_hwnd}.")

        down_input = self._create_vk_input(vk, is_down=True, extended=extended)
        up_input = self._create_vk_input(vk, is_down=False, extended=extended)

        sent = self._send_inputs([down_input, up_input])
        if sent != 2:
            raise InputFailedError(f"SendInput dispatched {sent} of 2 key events for '{norm_key}'.")

        return {
            "success": True,
            "action": "press_key",
            "key": norm_key,
            "hwnd": target_hwnd,
            "status": "success",
        }

    def send_shortcut(self, shortcut: str, target_hwnd: int) -> Dict[str, Any]:
        """Execute an approved keyboard shortcut in the target window.

        Guarantees that all pressed modifier keys are released in a finally block.

        Args:
            shortcut: Name of the shortcut (e.g. 'Ctrl+C', 'Ctrl+V', 'Shift+Left').
            target_hwnd: Positive HWND of the validated target window.

        Returns:
            Dict[str, Any]: Execution outcome.

        Raises:
            ProhibitedShortcutError: If shortcut is in the prohibited list.
            InvalidShortcutError: If shortcut is not in the approved catalog.
            TargetNotActiveError: If target window is not foreground.
            InputFailedError: If SendInput fails.
        """
        if not isinstance(shortcut, str) or not shortcut.strip():
            raise InvalidShortcutError(f"Shortcut must be a non-empty string, got: {shortcut!r}")

        # Normalize shortcut (e.g. "Ctrl + C" -> "ctrl+c")
        norm_shortcut = "+".join(part.strip().lower() for part in shortcut.split("+"))

        if norm_shortcut in PROHIBITED_SHORTCUTS:
            raise ProhibitedShortcutError(
                f"Shortcut '{shortcut}' is explicitly prohibited for system safety."
            )

        if norm_shortcut not in APPROVED_SHORTCUTS:
            raise InvalidShortcutError(
                f"Shortcut '{shortcut}' is not in approved shortcuts catalog. "
                f"Approved shortcuts: {sorted(list(APPROVED_SHORTCUTS.keys()))}."
            )

        modifiers, key_vk = APPROVED_SHORTCUTS[norm_shortcut]
        extended = key_vk in EXTENDED_VKS

        # Emergency stop pre-execution check
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            self.release_all_modifiers()
            manager.check_emergency_stop()

        # Prepare and activate window
        self._prepare_target_window(target_hwnd)

        logger.info(f"Executing approved shortcut '{norm_shortcut}' in HWND {target_hwnd}.")

        modifiers_pressed: List[int] = []
        try:
            # 1. Press modifier keys in order
            for mod_vk in modifiers:
                inp = self._create_vk_input(mod_vk, is_down=True, extended=False)
                sent = self._send_inputs([inp])
                if sent != 1:
                    raise InputFailedError(f"Failed to press modifier key 0x{mod_vk:02X}.")
                modifiers_pressed.append(mod_vk)

            # 2. Press and release primary key
            down_inp = self._create_vk_input(key_vk, is_down=True, extended=extended)
            up_inp = self._create_vk_input(key_vk, is_down=False, extended=extended)
            sent = self._send_inputs([down_inp, up_inp])
            if sent != 2:
                raise InputFailedError(f"Failed to press primary key 0x{key_vk:02X}.")

        finally:
            # 3. Unconditionally release modifiers in reverse order
            for mod_vk in reversed(modifiers_pressed):
                try:
                    rel_inp = self._create_vk_input(mod_vk, is_down=False, extended=False)
                    self._send_inputs([rel_inp])
                except Exception as cleanup_err:
                    logger.debug(f"Error releasing modifier key 0x{mod_vk:02X}: {cleanup_err}")

        return {
            "success": True,
            "action": "send_shortcut",
            "shortcut": norm_shortcut,
            "hwnd": target_hwnd,
            "status": "success",
        }

