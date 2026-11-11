"""
VoxPilot - Customizable Global Voice Shortcut Listener
Provides OS-level Windows hotkey registration (RegisterHotKey + Win32 message loop)
to toggle Voice Mode on/off even when another Windows application is in the foreground.
Enforces strict Emergency Stop ('Ctrl+Alt+Escape') reservation and automatic rollback on conflict.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
import time
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger("voxpilot.voice.hotkey")

# Win32 Constants
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

HOTKEY_ID_VOICE_MODE = 2
VK_ESCAPE = 0x1B

DEFAULT_VOICE_SHORTCUT = "Ctrl+Shift+V"

# Virtual Key Code Mapping for customizable shortcuts
SPECIAL_VK_MAP: Dict[str, Tuple[str, int]] = {
    "SPACE": ("Space", 0x20),
    "SPACEBAR": ("Space", 0x20),
    "TAB": ("Tab", 0x09),
    "ENTER": ("Enter", 0x0D),
    "RETURN": ("Enter", 0x0D),
    "BACKSPACE": ("Backspace", 0x08),
    "BS": ("Backspace", 0x08),
    "INSERT": ("Insert", 0x2D),
    "INS": ("Insert", 0x2D),
    "DELETE": ("Delete", 0x2E),
    "DEL": ("Delete", 0x2E),
    "HOME": ("Home", 0x24),
    "END": ("End", 0x23),
    "PAGEUP": ("PageUp", 0x21),
    "PGUP": ("PageUp", 0x21),
    "PAGEDOWN": ("PageDown", 0x22),
    "PGDN": ("PageDown", 0x22),
    "UP": ("Up", 0x26),
    "ARROWUP": ("Up", 0x26),
    "DOWN": ("Down", 0x28),
    "ARROWDOWN": ("Down", 0x28),
    "LEFT": ("Left", 0x25),
    "ARROWLEFT": ("Left", 0x25),
    "RIGHT": ("Right", 0x27),
    "ARROWRIGHT": ("Right", 0x27),
    "ESC": ("Escape", VK_ESCAPE),
    "ESCAPE": ("Escape", VK_ESCAPE),
    ";": (";", 0xBA),
    "SEMICOLON": (";", 0xBA),
    "=": ("=", 0xBB),
    "PLUS": ("=", 0xBB),
    ",": (",", 0xBC),
    "COMMA": (",", 0xBC),
    "-": ("-", 0xBD),
    "MINUS": ("-", 0xBD),
    ".": (".", 0xBE),
    "PERIOD": (".", 0xBE),
    "/": ("/", 0xBF),
    "SLASH": ("/", 0xBF),
    "`": ("`", 0xC0),
    "BACKQUOTE": ("`", 0xC0),
    "[": ("[", 0xDB),
    "\\ ": ("\\", 0xDC),
    "\\": ("\\", 0xDC),
    "]": ("]", 0xDD),
    "'": ("'", 0xDE),
}

# Add F1..F12 (0x70..0x7B)
for _f_idx in range(1, 13):
    SPECIAL_VK_MAP[f"F{_f_idx}"] = (f"F{_f_idx}", 0x70 + _f_idx - 1)


class ShortcutValidationError(ValueError):
    """Raised when a shortcut string is malformed or unsupported."""
    pass


class ReservedShortcutError(ShortcutValidationError):
    """Raised when attempting to assign a reserved system safety shortcut (Ctrl+Alt+Escape)."""
    pass


class ShortcutConflictError(RuntimeError):
    """Raised when Windows RegisterHotKey fails due to conflict with another registered hotkey."""
    pass


def parse_shortcut(shortcut_str: str) -> Tuple[str, int, int]:
    """
    Parse and validate a human-readable keyboard shortcut string into its canonical
    display form, Win32 modifier bitmask (excluding MOD_NOREPEAT), and Virtual-Key code.

    Args:
        shortcut_str: Shortcut string such as "Ctrl+Shift+V" or "Ctrl + Alt + M".

    Returns:
        Tuple[str, int, int]: (canonical_shortcut, modifiers_bitmask, vk_code)

    Raises:
        ReservedShortcutError: If the shortcut is Ctrl+Alt+Escape (reserved for Emergency Stop).
        ShortcutValidationError: If the shortcut format, modifiers, or key are invalid.
    """
    if not shortcut_str or not isinstance(shortcut_str, str) or not shortcut_str.strip():
        raise ShortcutValidationError("Shortcut cannot be empty.")

    raw_parts = [part.strip() for part in shortcut_str.replace("-", "+").split("+") if part.strip()]
    # Special case if user ended with '+' or '-' (e.g. "Ctrl+-")
    if shortcut_str.strip().endswith("-") and (not raw_parts or raw_parts[-1] != "-"):
        raw_parts.append("-")

    if not raw_parts:
        raise ShortcutValidationError("Shortcut must include a valid key combination.")

    modifiers = 0
    has_ctrl = False
    has_alt = False
    has_shift = False
    has_win = False
    primary_keys = []

    for token in raw_parts:
        upper = token.upper()
        if upper in ("CTRL", "CONTROL", "CTL"):
            has_ctrl = True
            modifiers |= MOD_CONTROL
        elif upper in ("ALT", "OPTION", "MENU"):
            has_alt = True
            modifiers |= MOD_ALT
        elif upper in ("SHIFT", "SHFT"):
            has_shift = True
            modifiers |= MOD_SHIFT
        elif upper in ("WIN", "WINDOWS", "META", "SUPER", "CMD", "COMMAND"):
            has_win = True
            modifiers |= MOD_WIN
        else:
            primary_keys.append(token)

    if len(primary_keys) == 0:
        raise ShortcutValidationError(
            f"Shortcut '{shortcut_str}' must include a non-modifier key (for example: Ctrl+Shift+V)."
        )
    if len(primary_keys) > 1:
        raise ShortcutValidationError(
            f"Shortcut '{shortcut_str}' contains multiple primary keys ({primary_keys}). Only one primary key is allowed."
        )

    key_token = primary_keys[0].strip()
    key_upper = key_token.upper()

    canonical_key: str
    vk_code: int

    if len(key_upper) == 1 and "A" <= key_upper <= "Z":
        canonical_key = key_upper
        vk_code = ord(key_upper)
    elif len(key_upper) == 1 and "0" <= key_upper <= "9":
        canonical_key = key_upper
        vk_code = ord(key_upper)
    elif key_upper in SPECIAL_VK_MAP:
        canonical_key, vk_code = SPECIAL_VK_MAP[key_upper]
    else:
        raise ShortcutValidationError(
            f"Unsupported key '{key_token}' in shortcut '{shortcut_str}'. "
            "Use letters (A-Z), digits (0-9), function keys (F1-F12), or standard keys (Space, Tab, Up, Down)."
        )

    is_function_key = canonical_key.startswith("F") and canonical_key[1:].isdigit()
    if modifiers == 0 and not is_function_key:
        raise ShortcutValidationError(
            f"Shortcut '{shortcut_str}' must include at least one modifier key (Ctrl, Alt, Shift, or Win)."
        )

    # CRITICAL SAFETY INVARIANT: Never allow replacing or conflicting with Global Emergency Stop (Ctrl+Alt+Escape)
    if modifiers == (MOD_CONTROL | MOD_ALT) and vk_code == VK_ESCAPE:
        raise ReservedShortcutError(
            "Shortcut 'Ctrl+Alt+Escape' is permanently reserved for the Global Emergency Stop "
            "and cannot be assigned to Voice Mode."
        )

    # Build canonical string in consistent order: Ctrl + Alt + Shift + Win + Key
    canonical_parts = []
    if has_ctrl:
        canonical_parts.append("Ctrl")
    if has_alt:
        canonical_parts.append("Alt")
    if has_shift:
        canonical_parts.append("Shift")
    if has_win:
        canonical_parts.append("Win")
    canonical_parts.append(canonical_key)

    canonical_str = "+".join(canonical_parts)
    return canonical_str, modifiers, vk_code


class VoiceHotkeyListener:
    """
    Registers and listens for the global Windows Voice Mode toggle shortcut
    on a dedicated daemon thread using Win32 RegisterHotKey and GetMessageW.
    """

    def __init__(
        self,
        callback: Callable[[str], None],
        shortcut: str = DEFAULT_VOICE_SHORTCUT,
        enabled: bool = True,
        debounce_seconds: float = 0.35,
    ) -> None:
        self._callback = callback
        canonical, mods, vk = parse_shortcut(shortcut)
        self._shortcut: str = canonical
        self._modifiers: int = mods
        self._vk_code: int = vk
        self._enabled: bool = bool(enabled)
        self._debounce_seconds: float = debounce_seconds

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._is_registered: bool = False
        self._registration_error: Optional[str] = None
        self._last_trigger_monotonic: float = 0.0
        self._trigger_count: int = 0

    @property
    def shortcut(self) -> str:
        with self._lock:
            return self._shortcut

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def is_registered(self) -> bool:
        with self._lock:
            return self._is_registered

    @property
    def registration_error(self) -> Optional[str]:
        with self._lock:
            return self._registration_error

    @property
    def trigger_count(self) -> int:
        with self._lock:
            return self._trigger_count

    def get_status(self) -> Dict[str, object]:
        """Return structured status of the global voice shortcut listener."""
        with self._lock:
            return {
                "shortcut": self._shortcut,
                "enabled": self._enabled,
                "is_registered": self._is_registered,
                "registration_error": self._registration_error,
                "trigger_count": self._trigger_count,
                "reserved_emergency_shortcut": "Ctrl+Alt+Escape",
            }

    def start(self) -> bool:
        """
        Start the hotkey listener thread and register the configured shortcut on Windows.
        If disabled or already registered, returns gracefully without duplicate registration.
        """
        with self._lock:
            if not self._enabled:
                self._is_registered = False
                self._registration_error = None
                return False

            if sys.platform != "win32":
                self._is_registered = False
                self._registration_error = "Global hotkeys are only supported on Windows (win32)."
                logger.warning(self._registration_error)
                return False

            # Prevent duplicate registration if already running and registered
            if self._thread is not None and self._thread.is_alive() and self._is_registered:
                return True

            # Ensure any stale thread is cleaned up first
            self._stop_thread_unlocked()

            self._stop_event.clear()
            self._ready_event.clear()
            self._is_registered = False
            self._registration_error = None

            self._thread = threading.Thread(
                target=self._message_loop,
                args=(self._shortcut, self._modifiers, self._vk_code),
                name="VoxPilot-VoiceHotkeyListener",
                daemon=True,
            )
            self._thread.start()

        self._ready_event.wait(timeout=2.5)
        with self._lock:
            return self._is_registered

    def stop(self) -> None:
        """Stop the hotkey listener thread and unregister the shortcut cleanly."""
        with self._lock:
            self._stop_thread_unlocked()

    def _stop_thread_unlocked(self) -> None:
        """Internal helper to stop the Win32 message loop thread while holding _lock."""
        self._stop_event.set()
        thread = self._thread
        thread_id = self._thread_id
        if thread is not None and thread.is_alive():
            if thread_id is not None and sys.platform == "win32":
                try:
                    ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                except Exception as exc:
                    logger.debug(f"Failed to post WM_QUIT to voice hotkey thread: {exc}")
            thread.join(timeout=2.5)

        self._thread = None
        self._thread_id = None
        self._is_registered = False

    def set_enabled(self, enabled: bool) -> bool:
        """Enable or disable the global voice shortcut dynamically."""
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._stop_thread_unlocked()
                self._registration_error = None
                return True
            return self.start()

    def update_shortcut(self, new_shortcut: str, enabled: Optional[bool] = None) -> str:
        """
        Validate and apply a new keyboard shortcut dynamically.
        If registration fails (e.g., hotkey conflict with another application),
        automatically restores and re-registers the previous valid shortcut.

        Args:
            new_shortcut: New shortcut string (e.g. "Ctrl+Shift+V" or "Ctrl+Alt+M").
            enabled: Optional boolean to update enabled state simultaneously.

        Returns:
            str: Canonical shortcut string after validation and registration.

        Raises:
            ReservedShortcutError: If new_shortcut is Ctrl+Alt+Escape.
            ShortcutValidationError: If new_shortcut is invalid.
            ShortcutConflictError: If Windows fails to register the shortcut while enabled.
        """
        canonical, new_mods, new_vk = parse_shortcut(new_shortcut)

        with self._lock:
            prev_shortcut = self._shortcut
            prev_mods = self._modifiers
            prev_vk = self._vk_code
            prev_enabled = self._enabled
            was_running = self._thread is not None and self._thread.is_alive() and self._is_registered

            target_enabled = prev_enabled if enabled is None else bool(enabled)

            # If unchanged and already in desired state, return immediately
            if (
                canonical == prev_shortcut
                and target_enabled == prev_enabled
                and (not target_enabled or was_running)
            ):
                return canonical

            # Unregister previous hotkey first
            self._stop_thread_unlocked()

            self._shortcut = canonical
            self._modifiers = new_mods
            self._vk_code = new_vk
            self._enabled = target_enabled

            if not target_enabled:
                self._registration_error = None
                return canonical

            # Attempt to register the new shortcut
            registered = self.start()
            if registered:
                return canonical

            # Registration failed! Capture the error and roll back to previous valid shortcut
            failed_error = self._registration_error or "Hotkey is already registered by another process."
            logger.warning(
                f"Shortcut '{canonical}' registration failed ({failed_error}). "
                f"Restoring previous shortcut '{prev_shortcut}'."
            )

            self._stop_thread_unlocked()
            self._shortcut = prev_shortcut
            self._modifiers = prev_mods
            self._vk_code = prev_vk
            self._enabled = prev_enabled

            if prev_enabled and was_running:
                self.start()

            raise ShortcutConflictError(
                f"Could not register shortcut '{canonical}': {failed_error} "
                f"Previous shortcut '{prev_shortcut}' remains active."
            )

    def _message_loop(self, shortcut_name: str, modifiers: int, vk_code: int) -> None:
        """Dedicated Win32 message loop for RegisterHotKey and WM_HOTKEY dispatch."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()

        msg = wintypes.MSG()
        # Force creation of thread message queue before RegisterHotKey
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        reg_flags = modifiers | MOD_NOREPEAT
        success = user32.RegisterHotKey(
            None,
            HOTKEY_ID_VOICE_MODE,
            reg_flags,
            vk_code,
        )

        if not success:
            err_code = ctypes.get_last_error() or kernel32.GetLastError()
            err_msg = f"Windows RegisterHotKey failed for '{shortcut_name}' (WinError {err_code})."
            self._is_registered = False
            self._registration_error = err_msg
            logger.warning(err_msg)
            self._ready_event.set()
            return

        self._is_registered = True
        self._registration_error = None

        logger.info(f"Global Voice Mode hotkey registered: {shortcut_name}")
        self._ready_event.set()

        try:
            while not self._stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:  # WM_QUIT (0) or error (-1)
                    break

                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_VOICE_MODE:
                    now = time.monotonic()
                    should_fire = False
                    with self._lock:
                        if now - self._last_trigger_monotonic >= self._debounce_seconds:
                            self._last_trigger_monotonic = now
                            self._trigger_count += 1
                            should_fire = True

                    if should_fire:
                        logger.info(f"Global Voice Mode hotkey '{shortcut_name}' pressed!")
                        try:
                            self._callback(shortcut_name)
                        except Exception as cb_err:
                            logger.error(f"Error in voice hotkey callback: {cb_err}", exc_info=True)

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID_VOICE_MODE)
            self._is_registered = False
            logger.info(f"Global Voice Mode hotkey unregistered: {shortcut_name}")
