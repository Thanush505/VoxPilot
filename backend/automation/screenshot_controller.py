"""
VoxPilot - Windows Desktop and Window Screenshot Subsystem
Provides safe, allowlisted, privacy-preserving screenshot capture for Windows 11.
Supports full multi-monitor desktop, primary monitor, specific monitor, and window capture
with output directory containment, overwrite guards, and window state validation.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any, Union

from PIL import Image, ImageGrab

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

logger = logging.getLogger(__name__)


# ==============================================================================
# Security Boundaries and Output Directory Defaults
# ==============================================================================

def get_default_screenshot_directory() -> Path:
    """Retrieve or create the default approved directory for saved screenshots."""
    pictures = Path.home() / "Pictures" / "VoxPilot_Screenshots"
    pictures.mkdir(parents=True, exist_ok=True)
    return pictures.resolve()


SAFE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg)$", re.IGNORECASE)


# ==============================================================================
# Exceptions
# ==============================================================================

class ScreenshotError(Exception):
    """Base exception for screenshot capture operations."""
    pass


class OutputDirectoryNotAllowedError(ScreenshotError):
    """Raised when destination path is outside approved output directories."""
    pass


class ScreenshotOverwriteError(ScreenshotError):
    """Raised when screenshot destination already exists and overwrite is not confirmed."""
    def __init__(self, message: str, target_path: str, requires_confirmation: bool = True):
        super().__init__(message)
        self.target_path = target_path
        self.requires_confirmation = requires_confirmation


class WindowCaptureError(ScreenshotError):
    """Raised when window capture fails (missing, minimized, closed, or hung)."""
    pass


class InvalidMonitorError(ScreenshotError):
    """Raised when an invalid or out-of-range monitor index is requested."""
    pass


class InvalidFilenameError(ScreenshotError):
    """Raised when screenshot filename contains invalid characters or traversal."""
    pass


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass(frozen=True)
class ScreenshotInfo:
    """Metadata describing a successfully captured and saved screenshot."""
    saved_path: str
    image_width: int
    image_height: int
    size_bytes: int
    capture_type: str
    target_hwnd: Optional[int]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "saved_path": self.saved_path,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "size_bytes": self.size_bytes,
            "capture_type": self.capture_type,
            "target_hwnd": self.target_hwnd,
            "timestamp": self.timestamp,
        }


# ==============================================================================
# Screenshot Controller
# ==============================================================================

class ScreenshotController:
    """Safe Windows desktop and window screenshot capture controller.

    Enforces target window allowlisting and responsiveness checks, validates
    destination paths against approved output directories, prevents silent overwrites,
    and returns comprehensive image metadata.
    """

    def __init__(
        self,
        output_directory: Optional[Union[str, Path]] = None,
        window_manager: Optional[WindowManager] = None,
    ) -> None:
        """Initialize ScreenshotController with approved output directory and WindowManager.

        Args:
            output_directory: Optional directory to store captured screenshots.
                              Defaults to ~/Pictures/VoxPilot_Screenshots.
            window_manager: Optional WindowManager instance for window validation.
        """
        if output_directory is not None:
            self._output_directory = Path(output_directory).resolve()
            self._output_directory.mkdir(parents=True, exist_ok=True)
        else:
            self._output_directory = get_default_screenshot_directory()

        self._window_manager = window_manager if window_manager is not None else WindowManager()
        logger.info(f"ScreenshotController initialized. Output directory: {self._output_directory}")

    @property
    def output_directory(self) -> Path:
        """Return canonical output directory."""
        return self._output_directory

    # --------------------------------------------------------------------------
    # Output Path Validation and Generation
    # --------------------------------------------------------------------------

    def _resolve_output_path(
        self,
        filename: Optional[str] = None,
        capture_type: str = "desktop",
        confirm_overwrite: bool = False,
    ) -> Path:
        """Resolve, sanitize, and validate the output screenshot destination.

        Args:
            filename: Optional user-specified filename. If None, auto-generates timestamped name.
            capture_type: Tag for auto-generated filename (e.g. 'desktop', 'window').
            confirm_overwrite: Whether overwriting an existing file is confirmed.

        Returns:
            Path: Canonical destination path inside approved output directory.
        """
        now = datetime.now(timezone.utc)
        if filename is None or not filename.strip():
            ts_str = now.strftime("%Y%m%d_%H%M%S_%f")
            safe_name = f"screenshot_{capture_type}_{ts_str}.png"
        else:
            clean_name = filename.strip()
            # Prevent directory traversal in filename
            if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
                raise InvalidFilenameError(f"Filename cannot contain path separators or traversal: {filename!r}")
            if not SAFE_FILENAME_PATTERN.match(clean_name):
                raise InvalidFilenameError(
                    f"Filename '{filename}' is invalid. Must be alphanumeric with .png, .jpg, or .jpeg extension."
                )
            safe_name = clean_name

        dest_path = (self._output_directory / safe_name).resolve()

        # Verify output directory containment
        try:
            if not dest_path.is_relative_to(self._output_directory):
                raise OutputDirectoryNotAllowedError(
                    f"Resolved destination '{dest_path}' escapes approved output directory '{self._output_directory}'."
                )
        except (ValueError, TypeError) as err:
            raise OutputDirectoryNotAllowedError(f"Containment check failed: {err}") from err

        # Overwrite check
        if dest_path.exists() and not confirm_overwrite:
            raise ScreenshotOverwriteError(
                f"Screenshot destination '{dest_path.name}' already exists. Overwrite requires confirmation.",
                target_path=str(dest_path),
                requires_confirmation=True,
            )

        return dest_path

    def _save_image(
        self,
        img: Image.Image,
        dest_path: Path,
        capture_type: str,
        target_hwnd: Optional[int] = None,
    ) -> ScreenshotInfo:
        """Save a PIL Image to disk and return structured ScreenshotInfo."""
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            # Determine format
            ext = dest_path.suffix.lower()
            img_format = "JPEG" if ext in (".jpg", ".jpeg") else "PNG"
            if img_format == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.save(dest_path, format=img_format)
            size_bytes = dest_path.stat().st_size
            width, height = img.size

            logger.info(
                f"Screenshot saved: '{dest_path.name}' ({width}x{height}, {size_bytes} bytes, type={capture_type})."
            )

            return ScreenshotInfo(
                saved_path=str(dest_path),
                image_width=width,
                image_height=height,
                size_bytes=size_bytes,
                capture_type=capture_type,
                target_hwnd=target_hwnd,
                timestamp=now_ts,
            )
        except Exception as err:
            raise ScreenshotError(f"Failed to save screenshot image to '{dest_path}': {err}") from err

    # --------------------------------------------------------------------------
    # 1. Capture Complete Desktop
    # --------------------------------------------------------------------------

    def capture_desktop(
        self,
        filename: Optional[str] = None,
        confirm_overwrite: bool = False,
    ) -> ScreenshotInfo:
        """Capture the complete Windows virtual desktop across all connected monitors.

        Args:
            filename: Optional destination filename.
            confirm_overwrite: Explicit flag if destination already exists.

        Returns:
            ScreenshotInfo: Metadata of saved screenshot.
        """
        dest_path = self._resolve_output_path(
            filename=filename,
            capture_type="desktop",
            confirm_overwrite=confirm_overwrite,
        )

        try:
            # ImageGrab.grab(all_screens=True) grabs entire virtual multi-monitor desktop
            img = ImageGrab.grab(all_screens=True)
            if img is None:
                raise ScreenshotError("ImageGrab returned None for desktop capture.")
            return self._save_image(img, dest_path, capture_type="desktop")
        except Exception as err:
            if isinstance(err, ScreenshotError):
                raise
            raise ScreenshotError(f"Desktop screenshot capture failed: {err}") from err

    # --------------------------------------------------------------------------
    # 2. Capture Primary Monitor
    # --------------------------------------------------------------------------

    def capture_primary_monitor(
        self,
        filename: Optional[str] = None,
        confirm_overwrite: bool = False,
    ) -> ScreenshotInfo:
        """Capture only the primary monitor display.

        Args:
            filename: Optional destination filename.
            confirm_overwrite: Explicit flag if destination already exists.

        Returns:
            ScreenshotInfo: Metadata of saved screenshot.
        """
        dest_path = self._resolve_output_path(
            filename=filename,
            capture_type="primary_monitor",
            confirm_overwrite=confirm_overwrite,
        )

        try:
            # Locate primary monitor bounding box
            primary_bbox = self._get_primary_monitor_rect()
            img = ImageGrab.grab(bbox=primary_bbox, all_screens=True)
            if img is None:
                raise ScreenshotError("ImageGrab returned None for primary monitor capture.")
            return self._save_image(img, dest_path, capture_type="primary_monitor")
        except Exception as err:
            if isinstance(err, ScreenshotError):
                raise
            raise ScreenshotError(f"Primary monitor screenshot capture failed: {err}") from err

    def _get_primary_monitor_rect(self) -> Tuple[int, int, int, int]:
        """Retrieve bounding box (left, top, right, bottom) of the primary display monitor."""
        if win32api is not None:
            try:
                monitors = win32api.EnumDisplayMonitors()
                for h_mon, hdc, rect in monitors:
                    info = win32api.GetMonitorInfo(h_mon)
                    # Flags & 1 == MONITORINFOF_PRIMARY
                    if info.get("Flags", 0) & 1:
                        return info["Monitor"]  # (left, top, right, bottom)
            except Exception as err:
                logger.debug(f"EnumDisplayMonitors failed: {err}")

        # Fallback to system metrics via ctypes
        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            width = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
            height = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
            return (0, 0, width, height)

        return (0, 0, 1920, 1080)

    # --------------------------------------------------------------------------
    # 3. Capture Selected Monitor
    # --------------------------------------------------------------------------

    def get_monitors_info(self) -> List[Dict[str, Any]]:
        """List all connected display monitors and their geometry."""
        monitors_list: List[Dict[str, Any]] = []

        if win32api is not None:
            try:
                monitors = win32api.EnumDisplayMonitors()
                for idx, (h_mon, hdc, rect) in enumerate(monitors, start=1):
                    info = win32api.GetMonitorInfo(h_mon)
                    m_rect = info.get("Monitor", rect)
                    is_primary = bool(info.get("Flags", 0) & 1)
                    monitors_list.append({
                        "index": idx,
                        "is_primary": is_primary,
                        "rect": {
                            "left": m_rect[0],
                            "top": m_rect[1],
                            "right": m_rect[2],
                            "bottom": m_rect[3],
                            "width": m_rect[2] - m_rect[0],
                            "height": m_rect[3] - m_rect[1],
                        },
                    })
                return monitors_list
            except Exception as err:
                logger.debug(f"EnumDisplayMonitors failed: {err}")

        # Fallback single monitor
        rect = self._get_primary_monitor_rect()
        return [{
            "index": 1,
            "is_primary": True,
            "rect": {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            },
        }]

    def capture_monitor(
        self,
        monitor_index: int,
        filename: Optional[str] = None,
        confirm_overwrite: bool = False,
    ) -> ScreenshotInfo:
        """Capture a specific display monitor by 1-based index.

        Args:
            monitor_index: 1-based index of the monitor to capture.
            filename: Optional destination filename.
            confirm_overwrite: Explicit flag if destination already exists.

        Returns:
            ScreenshotInfo: Metadata of saved screenshot.
        """
        monitors = self.get_monitors_info()
        if not (1 <= monitor_index <= len(monitors)):
            raise InvalidMonitorError(
                f"Monitor index {monitor_index} is invalid. Available monitors: 1 to {len(monitors)}."
            )

        target_mon = monitors[monitor_index - 1]
        r = target_mon["rect"]
        bbox = (r["left"], r["top"], r["right"], r["bottom"])

        dest_path = self._resolve_output_path(
            filename=filename,
            capture_type=f"monitor_{monitor_index}",
            confirm_overwrite=confirm_overwrite,
        )

        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            if img is None:
                raise ScreenshotError(f"ImageGrab returned None for monitor {monitor_index} capture.")
            return self._save_image(img, dest_path, capture_type=f"monitor_{monitor_index}")
        except Exception as err:
            if isinstance(err, ScreenshotError):
                raise
            raise ScreenshotError(f"Monitor {monitor_index} screenshot capture failed: {err}") from err

    # --------------------------------------------------------------------------
    # 4. Capture Specific Validated Application Window
    # --------------------------------------------------------------------------

    def capture_window(
        self,
        target_hwnd: int,
        filename: Optional[str] = None,
        confirm_overwrite: bool = False,
    ) -> ScreenshotInfo:
        """Capture the visual rectangle of an approved application window.

        Enforces allowlist ownership, window existence, responsiveness, and minimized checks.

        Args:
            target_hwnd: Handle of the target window to capture.
            filename: Optional destination filename.
            confirm_overwrite: Explicit flag if destination already exists.

        Returns:
            ScreenshotInfo: Metadata of saved screenshot.
        """
        if not isinstance(target_hwnd, int) or target_hwnd <= 0:
            raise WindowCaptureError(f"Invalid HWND: {target_hwnd!r}. Must be a positive integer.")

        # 1. Allowlist ownership verification
        try:
            self._window_manager.validate_window_ownership(target_hwnd)
        except WindowNotFoundError as err:
            raise WindowCaptureError(f"Target window not found: {err}") from err
        except WindowNotAllowedError as err:
            raise WindowCaptureError(f"Target window does not belong to an approved application: {err}") from err

        # 2. Check window existence
        if win32gui is not None and not win32gui.IsWindow(target_hwnd):
            raise WindowCaptureError(f"Window {target_hwnd} is not a valid or existing window.")

        # 3. Check window state (minimized windows cannot be visually captured)
        info = self._window_manager.get_window_info(target_hwnd)
        if info.is_minimized:
            raise WindowCaptureError(
                f"Window {target_hwnd} ('{info.title}') is minimized. Minimized windows cannot be captured."
            )

        # 4. Responsiveness verification
        if not self._window_manager.is_window_responsive(target_hwnd):
            raise WindowCaptureError(
                f"Window {target_hwnd} ('{info.title}') is unresponsive/hung and cannot be captured."
            )

        # 5. Determine window bounding box
        bbox = self._get_window_bounds(target_hwnd)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise WindowCaptureError(f"Invalid window bounding rectangle: {bbox}.")

        dest_path = self._resolve_output_path(
            filename=filename,
            capture_type=f"window_{target_hwnd}",
            confirm_overwrite=confirm_overwrite,
        )

        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            if img is None:
                raise ScreenshotError(f"ImageGrab returned None for window {target_hwnd} capture.")
            return self._save_image(img, dest_path, capture_type="window", target_hwnd=target_hwnd)
        except Exception as err:
            if isinstance(err, ScreenshotError):
                raise
            raise ScreenshotError(f"Window {target_hwnd} screenshot capture failed: {err}") from err

    def _get_window_bounds(self, hwnd: int) -> Tuple[int, int, int, int]:
        """Retrieve the true visual bounding rectangle for a window.

        Uses DwmGetWindowAttribute (DWMWA_EXTENDED_FRAME_BOUNDS) on Windows 10/11
        to exclude invisible window drop-shadows, falling back to GetWindowRect.
        """
        # Try DwmGetWindowAttribute with DWMWA_EXTENDED_FRAME_BOUNDS (9)
        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "dwmapi"):
            try:
                rect = wintypes.RECT()
                DWMWA_EXTENDED_FRAME_BOUNDS = 9
                res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
                    ctypes.byref(rect),
                    ctypes.sizeof(rect),
                )
                if res == 0:
                    return (rect.left, rect.top, rect.right, rect.bottom)
            except Exception as err:
                logger.debug(f"DwmGetWindowAttribute failed: {err}")

        # Fallback to win32gui.GetWindowRect
        if win32gui is not None:
            try:
                return win32gui.GetWindowRect(hwnd)
            except Exception as err:
                logger.debug(f"win32gui.GetWindowRect failed: {err}")

        return (0, 0, 800, 600)

