"""
VoxPilot - Mouse Automation Tool Interface
Provides Pydantic models and agent tool functions for safe, coordinate-validated,
window-scoped mouse interaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from backend.automation.window_manager import (
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

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class MoveMouseInput(BaseModel):
    """Pydantic schema for moving the mouse pointer."""
    x: int = Field(..., description="Target X-coordinate.")
    y: int = Field(..., description="Target Y-coordinate.")
    hwnd: Optional[int] = Field(
        None,
        description="Optional target window handle (HWND) for scoping coordinates.",
        gt=0,
    )
    relative_to_window: bool = Field(
        False,
        description="Whether (x, y) are offsets from target window top-left (requires hwnd).",
    )


class ClickMouseInput(BaseModel):
    """Pydantic schema for clicking the mouse inside an approved target window."""
    x: int = Field(..., description="Click X-coordinate.")
    y: int = Field(..., description="Click Y-coordinate.")
    hwnd: int = Field(
        ...,
        description="Mandatory target window handle (HWND) belonging to an approved application.",
        gt=0,
    )
    button: str = Field(
        "left",
        description="Mouse button to click ('left', 'right', or 'middle').",
    )
    clicks: int = Field(
        1,
        description="Number of clicks: 1 for single click, 2 for double click.",
        ge=1,
        le=2,
    )
    relative_to_window: bool = Field(
        False,
        description="Whether (x, y) are offsets from target window top-left.",
    )

    @field_validator("button")
    @classmethod
    def validate_button_name(cls, value: str) -> str:
        clean = value.strip().lower()
        if clean not in BUTTON_FLAGS:
            raise ValueError(
                f"Button '{value}' is not supported. Allowed: {list(BUTTON_FLAGS.keys())}"
            )
        return clean


class ScrollMouseInput(BaseModel):
    """Pydantic schema for scrolling the mouse wheel inside an approved target window."""
    amount: int = Field(
        ...,
        description="Scroll delta (positive for up/right, negative for down/left).",
        ge=MIN_SCROLL_AMOUNT,
        le=MAX_SCROLL_AMOUNT,
    )
    hwnd: int = Field(
        ...,
        description="Mandatory target window handle (HWND) belonging to an approved application.",
        gt=0,
    )
    x: Optional[int] = Field(None, description="Optional X-coordinate to position pointer before scrolling.")
    y: Optional[int] = Field(None, description="Optional Y-coordinate to position pointer before scrolling.")
    horizontal: bool = Field(False, description="Whether to scroll horizontally (HWHEEL) instead of vertically.")
    relative_to_window: bool = Field(False, description="Whether (x, y) are offsets from window top-left.")

    @field_validator("amount")
    @classmethod
    def validate_scroll_amount(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Scroll amount must be non-zero.")
        return value


class MouseActionResult(BaseModel):
    """Structured result returned by mouse automation tools."""
    success: bool
    action: str
    hwnd: Optional[int] = None
    coordinates: Optional[Dict[str, int]] = None
    button: Optional[str] = None
    clicks: Optional[int] = None
    amount: Optional[int] = None
    status: str
    message: str
    timestamp: str


# ==============================================================================
# Tool Functions
# ==============================================================================

def move_mouse_tool(
    data: MoveMouseInput,
    controller: Optional[MouseController] = None,
) -> MouseActionResult:
    """Move the mouse pointer with multi-monitor coordinate and window bounds validation.

    Args:
        data: Validated MoveMouseInput schema.
        controller: Optional MouseController instance.

    Returns:
        MouseActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else MouseController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = active_controller.move_to(
            x=data.x,
            y=data.y,
            target_hwnd=data.hwnd,
            relative_to_window=data.relative_to_window,
        )
        return MouseActionResult(
            success=True,
            action="move",
            hwnd=data.hwnd,
            coordinates=res.get("coordinates"),
            status="success",
            message=f"Mouse pointer moved successfully to ({res['coordinates']['x']}, {res['coordinates']['y']}).",
            timestamp=now_ts,
        )

    except (CoordinatesOutOfBoundsError, CoordinatesOutsideWindowError) as err:
        logger.warning(f"Coordinate boundary violation in move_mouse: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            coordinates={"x": data.x, "y": data.y},
            status="bounds_error",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        logger.warning(f"Target window not found: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        logger.warning(f"Target window not allowed: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowStateError as err:
        logger.warning(f"Target window in invalid state: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        logger.error(f"UIPI restriction in move_mouse: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidInputError, ValueError) as err:
        logger.warning(f"Invalid input in move_mouse: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        logger.error(f"Move input failed: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in move_mouse_tool: {err}")
        return MouseActionResult(
            success=False,
            action="move",
            hwnd=data.hwnd,
            status="error",
            message="An unexpected system error occurred while moving mouse.",
            timestamp=now_ts,
        )


def click_mouse_tool(
    data: ClickMouseInput,
    controller: Optional[MouseController] = None,
) -> MouseActionResult:
    """Click the mouse inside an approved target window.

    Args:
        data: Validated ClickMouseInput schema.
        controller: Optional MouseController instance.

    Returns:
        MouseActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else MouseController()
    now_ts = datetime.now(timezone.utc).isoformat()
    action_name = "click" if data.clicks == 1 else "double_click"

    try:
        res = active_controller.click(
            x=data.x,
            y=data.y,
            target_hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            relative_to_window=data.relative_to_window,
        )
        return MouseActionResult(
            success=True,
            action=action_name,
            hwnd=data.hwnd,
            coordinates=res.get("coordinates"),
            button=data.button,
            clicks=data.clicks,
            status="success",
            message=f"Successfully performed {action_name} ({data.button}) at ({res['coordinates']['x']}, {res['coordinates']['y']}) in window {data.hwnd}.",
            timestamp=now_ts,
        )

    except (CoordinatesOutOfBoundsError, CoordinatesOutsideWindowError) as err:
        logger.warning(f"Coordinate boundary violation in click_mouse: {err}")
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            coordinates={"x": data.x, "y": data.y},
            button=data.button,
            clicks=data.clicks,
            status="bounds_error",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowStateError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetWindowUnresponsiveError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except ActivationRestrictedError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="activation_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetNotActiveError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidMouseButtonError, InvalidClickCountError, InvalidInputError, ValueError) as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in click_mouse_tool: {err}")
        return MouseActionResult(
            success=False,
            action=action_name,
            hwnd=data.hwnd,
            button=data.button,
            clicks=data.clicks,
            status="error",
            message="An unexpected system error occurred while clicking mouse.",
            timestamp=now_ts,
        )


def scroll_mouse_tool(
    data: ScrollMouseInput,
    controller: Optional[MouseController] = None,
) -> MouseActionResult:
    """Scroll the mouse wheel inside an approved target window.

    Args:
        data: Validated ScrollMouseInput schema.
        controller: Optional MouseController instance.

    Returns:
        MouseActionResult: Structured outcome model.
    """
    active_controller = controller if controller is not None else MouseController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = active_controller.scroll(
            amount=data.amount,
            target_hwnd=data.hwnd,
            x=data.x,
            y=data.y,
            horizontal=data.horizontal,
            relative_to_window=data.relative_to_window,
        )
        return MouseActionResult(
            success=True,
            action="scroll",
            hwnd=data.hwnd,
            coordinates=res.get("coordinates"),
            amount=data.amount,
            status="success",
            message=f"Successfully scrolled {'horizontal' if data.horizontal else 'vertical'} by {data.amount} units in window {data.hwnd}.",
            timestamp=now_ts,
        )

    except (CoordinatesOutOfBoundsError, CoordinatesOutsideWindowError) as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="bounds_error",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotFoundError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="target_not_found",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowNotAllowedError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="target_not_allowed",
            message=str(err),
            timestamp=now_ts,
        )

    except WindowStateError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetWindowUnresponsiveError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except ActivationRestrictedError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="activation_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except TargetNotActiveError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="target_not_active",
            message=str(err),
            timestamp=now_ts,
        )

    except UIPIRestrictedError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="uipi_restricted",
            message=str(err),
            timestamp=now_ts,
        )

    except (InvalidInputError, ValueError) as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="invalid_input",
            message=str(err),
            timestamp=now_ts,
        )

    except InputFailedError as err:
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="input_failed",
            message=str(err),
            timestamp=now_ts,
        )

    except Exception as err:
        logger.exception(f"Unexpected error in scroll_mouse_tool: {err}")
        return MouseActionResult(
            success=False,
            action="scroll",
            hwnd=data.hwnd,
            amount=data.amount,
            status="error",
            message="An unexpected system error occurred while scrolling.",
            timestamp=now_ts,
        )

