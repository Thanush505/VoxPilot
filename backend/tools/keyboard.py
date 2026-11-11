"""
VoxPilot - Keyboard Automation Tool Interface
Provides Pydantic models and agent tool functions for safe, allowlisted,
window-scoped keyboard interaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field, field_validator

try:
    import win32gui
except ImportError:
    win32gui = None  # type: ignore

from backend.automation.window_manager import (
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
    DEFAULT_TYPING_INTERVAL,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class TypeTextInput(BaseModel):
    """Pydantic schema for typing text into an approved target window."""
    hwnd: Optional[int] = Field(
        None,
        description="Target window handle (HWND) belonging to an approved application. If omitted, target is resolved via application_name or active foreground window.",
        gt=0,
    )
    application_name: Optional[str] = Field(
        None,
        description="Optional name of the approved application to target (e.g. 'notepad').",
        max_length=100,
    )
    text: str = Field(
        ...,
        description="Text content to type into the target window.",
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
    )
    interval: float = Field(
        DEFAULT_TYPING_INTERVAL,
        description="Delay in seconds between typed characters (0.005s to 0.05s).",
        ge=MIN_TYPING_INTERVAL,
        le=MAX_TYPING_INTERVAL,
    )


class PressKeyInput(BaseModel):
    """Pydantic schema for pressing an individual approved key."""
    hwnd: Optional[int] = Field(
        None,
        description="Target window handle (HWND) belonging to an approved application. If omitted, target is resolved via application_name or active foreground window.",
        gt=0,
    )
    application_name: Optional[str] = Field(
        None,
        description="Optional name of the approved application to target (e.g. 'notepad').",
        max_length=100,
    )
    key: str = Field(
        ...,
        description=(
            "Approved key name to press (e.g. 'enter', 'tab', 'space', 'backspace', "
            "'delete', 'up', 'down', 'left', 'right', 'home', 'end', 'escape')."
        ),
        min_length=1,
        max_length=50,
    )

    @field_validator("key")
    @classmethod
    def validate_key_name(cls, value: str) -> str:
        clean = value.strip().lower()
        if clean not in APPROVED_KEYS:
            raise ValueError(
                f"Key '{value}' is not an approved key. Allowed: {sorted(list(APPROVED_KEYS.keys()))}"
            )
        return clean


class SendShortcutInput(BaseModel):
    """Pydantic schema for executing an approved keyboard shortcut."""
    hwnd: Optional[int] = Field(
        None,
        description="Target window handle (HWND) belonging to an approved application. If omitted, target is resolved via application_name or active foreground window.",
        gt=0,
    )
    application_name: Optional[str] = Field(
        None,
        description="Optional name of the approved application to target (e.g. 'notepad').",
        max_length=100,
    )
    shortcut: str = Field(
        ...,
        description=(
            "Approved keyboard shortcut (e.g. 'Ctrl+C', 'Ctrl+V', 'Ctrl+A', 'Ctrl+Z', "
            "'Ctrl+S', 'Shift+Left', 'Shift+Right')."
        ),
        min_length=1,
        max_length=50,
    )

    @field_validator("shortcut")
    @classmethod
    def validate_shortcut_name(cls, value: str) -> str:
        clean = "+".join(part.strip().lower() for part in value.split("+"))
        if clean in PROHIBITED_SHORTCUTS:
            raise ValueError(f"Shortcut '{value}' is explicitly prohibited for system safety.")
        if clean not in APPROVED_SHORTCUTS:
            raise ValueError(
                f"Shortcut '{value}' is not in approved shortcuts catalog. Allowed: {sorted(list(APPROVED_SHORTCUTS.keys()))}"
            )
        return clean


class KeyboardActionResult(BaseModel):
    """Structured result returned by keyboard automation tools."""
    success: bool
    action: str
    hwnd: int
    characters_typed: Optional[int] = None
    status: str
    message: str
    timestamp: str


# ==============================================================================
# Target Window Resolution Helper
# ==============================================================================

def _resolve_target_hwnd(
    hwnd: Optional[int],
    application_name: Optional[str],
) -> Tuple[int, Optional[str]]:
    """Resolve target HWND from explicit handle, application name, or foreground window.

    Returns:
        Tuple of (target_hwnd, error_message). If error_message is not None, resolution failed.
    """
    if hwnd is not None and hwnd > 0:
        return hwnd, None

    if application_name:
        wm = WindowManager()
        try:
            win_list = wm.find_windows(app_name=application_name, visible_only=True)
        except Exception as err:
            return 0, f"Failed searching for windows of '{application_name}': {err}"

        if not win_list:
            return 0, f"No open window found for approved application '{application_name}'."
        if len(win_list) > 1:
            return 0, f"Multiple windows found for '{application_name}'. Please specify target window."
        return win_list[0].hwnd, None

    # Fallback to active foreground window
    if win32gui is not None:
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd and fg_hwnd > 0:
                return fg_hwnd, None
        except Exception:
            pass

    return 0, "No target window handle provided and no active foreground window found."


# ==============================================================================
# Tool Functions
# ==============================================================================

def type_text_tool(
    data: TypeTextInput,
    controller: Optional[KeyboardController] = None,
) -> KeyboardActionResult:
    """Type text into an approved target application window.

    Args:
        data: Validated TypeTextInput schema.
        controller: Optional KeyboardController instance.

    Returns:
        KeyboardActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else KeyboardController()
    now_ts = datetime.now(timezone.utc).isoformat()

    target_hwnd, resolve_err = _resolve_target_hwnd(data.hwnd, data.application_name)
    if resolve_err or target_hwnd <= 0:
        logger.warning(f"Target window resolution failed: {resolve_err}")
        status = "target_not_found"
        if resolve_err and "Multiple" in resolve_err:
            status = "ambiguous_target"
        elif resolve_err and "no active foreground" in resolve_err.lower():
            status = "target_not_active"
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=0,
            status=status,
            message=resolve_err or "Invalid target window.",
            timestamp=now_ts,
        )

    try:
        # Never log raw data.text to prevent leaking sensitive content (passwords, PII)
        logger.info(f"type_text_tool invoked for HWND {target_hwnd} ({len(data.text)} characters).")

        res = active_controller.type_text(
            text=data.text,
            target_hwnd=target_hwnd,
            interval=data.interval,
        )
        return KeyboardActionResult(
            success=True,
            action="type_text",
            hwnd=target_hwnd,
            characters_typed=res.get("characters_typed", len(data.text)),
            status="success",
            message=f"Successfully typed {res.get('characters_typed', len(data.text))} characters into window {target_hwnd}.",
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        logger.warning(f"Target window not found: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        logger.warning(f"Target window rejected by allowlist: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetWindowUnresponsiveError as err:
        logger.warning(f"Target window unresponsive: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except ActivationRestrictedError as err:
        logger.warning(f"Activation restricted: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="activation_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetNotActiveError as err:
        logger.warning(f"Target not active: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except FocusLostError as err:
        logger.warning(f"Focus lost during typing: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            characters_typed=err.characters_typed,
            status="focus_lost",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        logger.error(f"UIPI restriction encountered: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidInputError, ValueError) as err:
        logger.warning(f"Invalid input: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        logger.error(f"Input dispatch failed: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in type_text_tool: {err}")
        return KeyboardActionResult(
            success=False,
            action="type_text",
            hwnd=target_hwnd,
            status="error",
            message="An unexpected system error occurred while typing text.",
            timestamp=now_ts,
        )


def press_key_tool(
    data: PressKeyInput,
    controller: Optional[KeyboardController] = None,
) -> KeyboardActionResult:
    """Press an approved key in the target window.

    Args:
        data: Validated PressKeyInput schema.
        controller: Optional KeyboardController instance.

    Returns:
        KeyboardActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else KeyboardController()
    now_ts = datetime.now(timezone.utc).isoformat()

    target_hwnd, resolve_err = _resolve_target_hwnd(data.hwnd, data.application_name)
    if resolve_err or target_hwnd <= 0:
        logger.warning(f"Target window resolution failed: {resolve_err}")
        status = "target_not_found"
        if resolve_err and "Multiple" in resolve_err:
            status = "ambiguous_target"
        elif resolve_err and "no active foreground" in resolve_err.lower():
            status = "target_not_active"
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=0,
            status=status,
            message=resolve_err or "Invalid target window.",
            timestamp=now_ts,
        )

    try:
        logger.info(f"press_key_tool invoked for key '{data.key}' in HWND {target_hwnd}.")
        active_controller.press_key(key=data.key, target_hwnd=target_hwnd)

        return KeyboardActionResult(
            success=True,
            action="press_key",
            hwnd=target_hwnd,
            status="success",
            message=f"Successfully pressed key '{data.key}' in window {target_hwnd}.",
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        logger.warning(f"Target window not found: {err}")
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        logger.warning(f"Target window rejected: {err}")
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetWindowUnresponsiveError as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except ActivationRestrictedError as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="activation_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetNotActiveError as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidKeyError, InvalidInputError, ValueError) as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in press_key_tool: {err}")
        return KeyboardActionResult(
            success=False,
            action="press_key",
            hwnd=target_hwnd,
            status="error",
            message="An unexpected system error occurred while pressing key.",
            timestamp=now_ts,
        )


def send_shortcut_tool(
    data: SendShortcutInput,
    controller: Optional[KeyboardController] = None,
) -> KeyboardActionResult:
    """Execute an approved keyboard shortcut in the target window.

    Args:
        data: Validated SendShortcutInput schema.
        controller: Optional KeyboardController instance.

    Returns:
        KeyboardActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else KeyboardController()
    now_ts = datetime.now(timezone.utc).isoformat()

    target_hwnd, resolve_err = _resolve_target_hwnd(data.hwnd, data.application_name)
    if resolve_err or target_hwnd <= 0:
        logger.warning(f"Target window resolution failed: {resolve_err}")
        status = "target_not_found"
        if resolve_err and "Multiple" in resolve_err:
            status = "ambiguous_target"
        elif resolve_err and "no active foreground" in resolve_err.lower():
            status = "target_not_active"
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=0,
            status=status,
            message=resolve_err or "Invalid target window.",
            timestamp=now_ts,
        )

    try:
        logger.info(f"send_shortcut_tool invoked for shortcut '{data.shortcut}' in HWND {target_hwnd}.")
        active_controller.send_shortcut(shortcut=data.shortcut, target_hwnd=target_hwnd)

        return KeyboardActionResult(
            success=True,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="success",
            message=f"Successfully executed shortcut '{data.shortcut}' in window {target_hwnd}.",
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetWindowUnresponsiveError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except ActivationRestrictedError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="activation_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetNotActiveError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidShortcutError, ProhibitedShortcutError, InvalidInputError, ValueError) as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in send_shortcut_tool: {err}")
        return KeyboardActionResult(
            success=False,
            action="send_shortcut",
            hwnd=target_hwnd,
            status="error",
            message="An unexpected system error occurred while executing shortcut.",
            timestamp=now_ts,
        )

