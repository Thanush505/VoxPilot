"""
VoxPilot - Windows Screenshot Tool Interface
Provides Pydantic models and agent tool functions for safe, privacy-preserving
desktop and window screenshot capture.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from backend.automation.screenshot_controller import (
    ScreenshotController,
    ScreenshotError,
    OutputDirectoryNotAllowedError,
    ScreenshotOverwriteError,
    WindowCaptureError,
    InvalidMonitorError,
    InvalidFilenameError,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Input Schemas
# ==============================================================================

class CaptureDesktopInput(BaseModel):
    """Pydantic schema for capturing full desktop or primary monitor."""
    filename: Optional[str] = Field(
        None,
        description="Optional filename (e.g. 'report.png'). If omitted, timestamped name is generated.",
        max_length=100,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation to overwrite if destination file already exists.",
    )


class CaptureMonitorInput(BaseModel):
    """Pydantic schema for capturing a specific display monitor."""
    monitor_index: int = Field(
        1,
        description="1-based index of the display monitor to capture.",
        ge=1,
        le=16,
    )
    filename: Optional[str] = Field(
        None,
        description="Optional filename (e.g. 'monitor2.png'). If omitted, timestamped name is generated.",
        max_length=100,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation to overwrite if destination file already exists.",
    )


class CaptureWindowInput(BaseModel):
    """Pydantic schema for capturing an approved application window."""
    hwnd: int = Field(
        ...,
        description="Mandatory target window handle (HWND) belonging to an approved application.",
        gt=0,
    )
    filename: Optional[str] = Field(
        None,
        description="Optional filename (e.g. 'notepad_screen.png'). If omitted, timestamped name is generated.",
        max_length=100,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation to overwrite if destination file already exists.",
    )


# ==============================================================================
# Pydantic Result Schemas
# ==============================================================================

class ScreenshotActionResult(BaseModel):
    """Structured result returned by screenshot capture tools."""
    success: bool
    capture_type: str
    target_hwnd: Optional[int] = None
    saved_path: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    size_bytes: Optional[int] = None
    requires_confirmation: bool = False
    status: str
    message: str
    timestamp: str
    monitors: Optional[List[Dict[str, Any]]] = None


# ==============================================================================
# Tool Functions
# ==============================================================================

def capture_desktop_tool(
    data: CaptureDesktopInput,
    controller: Optional[ScreenshotController] = None,
) -> ScreenshotActionResult:
    """Capture the full virtual desktop across all monitors."""
    ctrl = controller if controller is not None else ScreenshotController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info("capture_desktop_tool invoked.")
        info = ctrl.capture_desktop(
            filename=data.filename,
            confirm_overwrite=data.confirm_overwrite,
        )
        return ScreenshotActionResult(
            success=True,
            capture_type="desktop",
            saved_path=info.saved_path,
            image_width=info.image_width,
            image_height=info.image_height,
            size_bytes=info.size_bytes,
            requires_confirmation=False,
            status="success",
            message=f"Full desktop captured successfully ({info.image_width}x{info.image_height}).",
            timestamp=now_ts,
        )
    except ScreenshotOverwriteError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="desktop",
            saved_path=err.target_path,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (OutputDirectoryNotAllowedError, InvalidFilenameError) as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="desktop",
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="desktop",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def capture_primary_monitor_tool(
    data: CaptureDesktopInput,
    controller: Optional[ScreenshotController] = None,
) -> ScreenshotActionResult:
    """Capture only the primary monitor display."""
    ctrl = controller if controller is not None else ScreenshotController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info("capture_primary_monitor_tool invoked.")
        info = ctrl.capture_primary_monitor(
            filename=data.filename,
            confirm_overwrite=data.confirm_overwrite,
        )
        return ScreenshotActionResult(
            success=True,
            capture_type="primary_monitor",
            saved_path=info.saved_path,
            image_width=info.image_width,
            image_height=info.image_height,
            size_bytes=info.size_bytes,
            requires_confirmation=False,
            status="success",
            message=f"Primary monitor captured successfully ({info.image_width}x{info.image_height}).",
            timestamp=now_ts,
        )
    except ScreenshotOverwriteError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="primary_monitor",
            saved_path=err.target_path,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (OutputDirectoryNotAllowedError, InvalidFilenameError) as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="primary_monitor",
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="primary_monitor",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def capture_monitor_tool(
    data: CaptureMonitorInput,
    controller: Optional[ScreenshotController] = None,
) -> ScreenshotActionResult:
    """Capture a specific monitor by 1-based index."""
    ctrl = controller if controller is not None else ScreenshotController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info(f"capture_monitor_tool invoked for monitor {data.monitor_index}.")
        info = ctrl.capture_monitor(
            monitor_index=data.monitor_index,
            filename=data.filename,
            confirm_overwrite=data.confirm_overwrite,
        )
        return ScreenshotActionResult(
            success=True,
            capture_type=f"monitor_{data.monitor_index}",
            saved_path=info.saved_path,
            image_width=info.image_width,
            image_height=info.image_height,
            size_bytes=info.size_bytes,
            requires_confirmation=False,
            status="success",
            message=f"Monitor {data.monitor_index} captured successfully ({info.image_width}x{info.image_height}).",
            timestamp=now_ts,
        )
    except InvalidMonitorError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type=f"monitor_{data.monitor_index}",
            status="invalid_monitor",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotOverwriteError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type=f"monitor_{data.monitor_index}",
            saved_path=err.target_path,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (OutputDirectoryNotAllowedError, InvalidFilenameError) as err:
        return ScreenshotActionResult(
            success=False,
            capture_type=f"monitor_{data.monitor_index}",
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type=f"monitor_{data.monitor_index}",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def capture_window_tool(
    data: CaptureWindowInput,
    controller: Optional[ScreenshotController] = None,
) -> ScreenshotActionResult:
    """Capture a specific approved application window."""
    ctrl = controller if controller is not None else ScreenshotController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info(f"capture_window_tool invoked for HWND {data.hwnd}.")
        info = ctrl.capture_window(
            target_hwnd=data.hwnd,
            filename=data.filename,
            confirm_overwrite=data.confirm_overwrite,
        )
        return ScreenshotActionResult(
            success=True,
            capture_type="window",
            target_hwnd=data.hwnd,
            saved_path=info.saved_path,
            image_width=info.image_width,
            image_height=info.image_height,
            size_bytes=info.size_bytes,
            requires_confirmation=False,
            status="success",
            message=f"Window {data.hwnd} captured successfully ({info.image_width}x{info.image_height}).",
            timestamp=now_ts,
        )
    except WindowCaptureError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="window",
            target_hwnd=data.hwnd,
            status="window_capture_failed",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotOverwriteError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="window",
            target_hwnd=data.hwnd,
            saved_path=err.target_path,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (OutputDirectoryNotAllowedError, InvalidFilenameError) as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="window",
            target_hwnd=data.hwnd,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except ScreenshotError as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="window",
            target_hwnd=data.hwnd,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def list_monitors_tool(
    controller: Optional[ScreenshotController] = None,
) -> ScreenshotActionResult:
    """List all connected display monitors and their geometry."""
    ctrl = controller if controller is not None else ScreenshotController()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        monitors = ctrl.get_monitors_info()
        return ScreenshotActionResult(
            success=True,
            capture_type="list_monitors",
            status="success",
            message=f"Found {len(monitors)} connected monitors.",
            timestamp=now_ts,
            monitors=monitors,
        )
    except Exception as err:
        return ScreenshotActionResult(
            success=False,
            capture_type="list_monitors",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )

