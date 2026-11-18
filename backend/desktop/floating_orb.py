"""
VoxPilot - Global Floating Voice Orb Controller
Manages a frameless, transparent, always-on-top, non-focus-stealing PyWebView
companion window that displays system-wide voice status across Windows apps.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("voxpilot.desktop.floating_orb")

ORB_WINDOW_TITLE = "VoxPilot Voice Orb"
ORB_COLLAPSED_SIZE: Tuple[int, int] = (88, 88)
ORB_EXPANDED_SIZE: Tuple[int, int] = (296, 256)
ORB_SCREEN_MARGIN = 20

VALID_ORB_CORNERS = frozenset({"top_left", "top_right", "bottom_left", "bottom_right", "custom"})


@dataclass(frozen=True)
class MonitorWorkArea:
    """Represents a Windows monitor work area (excluding taskbar)."""
    index: int
    left: int
    top: int
    right: int
    bottom: int
    is_primary: bool = False

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def get_display_monitors() -> List[MonitorWorkArea]:
    """Enumerate display monitor work areas using Win32 EnumDisplayMonitors."""
    if sys.platform != "win32":
        return [MonitorWorkArea(index=0, left=0, top=0, right=1920, bottom=1040, is_primary=True)]

    try:
        import ctypes
        from ctypes import wintypes

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitors: List[MonitorWorkArea] = []
        MonitorEnumProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def _callback(hmonitor, _hdc, _rect, _data) -> bool:
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                work = info.rcWork
                monitors.append(
                    MonitorWorkArea(
                        index=len(monitors),
                        left=int(work.left),
                        top=int(work.top),
                        right=int(work.right),
                        bottom=int(work.bottom),
                        is_primary=bool(info.dwFlags & 1),
                    )
                )
            return True

        cb = MonitorEnumProc(_callback)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)
        if monitors:
            # Keep primary monitor first so default corner resolves on primary display
            monitors.sort(key=lambda m: (not m.is_primary, m.index))
            return monitors
    except Exception as err:
        logger.debug("Monitor enumeration fallback: %s", err)

    return [MonitorWorkArea(index=0, left=0, top=0, right=1920, bottom=1040, is_primary=True)]


def _pick_monitor_for_point(
    x: int,
    y: int,
    monitors: List[MonitorWorkArea],
) -> MonitorWorkArea:
    """Select the monitor containing (x, y), or the nearest monitor work area."""
    for mon in monitors:
        if mon.left <= x < mon.right and mon.top <= y < mon.bottom:
            return mon

    def _dist_sq(mon: MonitorWorkArea) -> int:
        cx = min(max(x, mon.left), mon.right)
        cy = min(max(y, mon.top), mon.bottom)
        return (x - cx) ** 2 + (y - cy) ** 2

    return min(monitors, key=_dist_sq)


def compute_orb_position(
    corner: str = "bottom_right",
    width: int = ORB_COLLAPSED_SIZE[0],
    height: int = ORB_COLLAPSED_SIZE[1],
    custom_x: Optional[int] = None,
    custom_y: Optional[int] = None,
    monitors: Optional[List[MonitorWorkArea]] = None,
    monitor_index: int = 0,
    margin: int = ORB_SCREEN_MARGIN,
) -> Tuple[int, int]:
    """Compute clamped screen coordinates for the floating orb across single/multi-monitor setups."""
    active_monitors = monitors if monitors else get_display_monitors()
    if not active_monitors:
        active_monitors = [MonitorWorkArea(index=0, left=0, top=0, right=1920, bottom=1040, is_primary=True)]

    safe_corner = (corner or "bottom_right").strip().lower()
    if safe_corner == "custom" and custom_x is not None and custom_y is not None:
        target = _pick_monitor_for_point(int(custom_x), int(custom_y), active_monitors)
        max_x = max(target.left, target.right - width)
        max_y = max(target.top, target.bottom - height)
        clamped_x = min(max(int(custom_x), target.left), max_x)
        clamped_y = min(max(int(custom_y), target.top), max_y)
        return clamped_x, clamped_y

    idx = monitor_index if 0 <= monitor_index < len(active_monitors) else 0
    mon = active_monitors[idx]

    if safe_corner == "top_left":
        x = mon.left + margin
        y = mon.top + margin
    elif safe_corner == "top_right":
        x = mon.right - width - margin
        y = mon.top + margin
    elif safe_corner == "bottom_left":
        x = mon.left + margin
        y = mon.bottom - height - margin
    else:
        # Default: bottom_right
        x = mon.right - width - margin
        y = mon.bottom - height - margin

    max_x = max(mon.left, mon.right - width)
    max_y = max(mon.top, mon.bottom - height)
    return min(max(x, mon.left), max_x), min(max(y, mon.top), max_y)


def find_orb_window_hwnd(title: str = ORB_WINDOW_TITLE) -> Optional[int]:
    """Locate the Win32 HWND for the floating orb window by title."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        return int(hwnd) if hwnd else None
    except Exception:
        return None


def apply_no_activate_topmost_style(hwnd: int) -> bool:
    """Apply WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST so the orb never steals focus."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010

        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        return True
    except Exception as err:
        logger.debug("Failed to apply WS_EX_NOACTIVATE on orb HWND %s: %s", hwnd, err)
        return False


def show_window_no_activate(hwnd: int) -> bool:
    """Show an existing Win32 window topmost without stealing keyboard or mouse focus."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes

        SW_SHOWNOACTIVATE = 4
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040

        user32 = ctypes.windll.user32
        apply_no_activate_topmost_style(hwnd)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        return True
    except Exception as err:
        logger.debug("Failed to show orb HWND %s without activation: %s", hwnd, err)
        return False


class FloatingOrbController:
    """Coordinates the single floating voice orb window, visibility, and multi-monitor placement."""

    def __init__(self, title: str = ORB_WINDOW_TITLE) -> None:
        self.title = title
        self._lock = threading.RLock()
        self._window: Any = None
        self._is_visible: bool = False
        self._panel_expanded: bool = False
        self._last_phase: str = "idle"
        self._last_is_active: bool = False
        self._last_position: Optional[Tuple[int, int]] = None
        self._destroyed: bool = False

    @property
    def has_window(self) -> bool:
        with self._lock:
            return self._window is not None and not self._destroyed

    @property
    def is_visible(self) -> bool:
        with self._lock:
            return self._is_visible

    @property
    def panel_expanded(self) -> bool:
        with self._lock:
            return self._panel_expanded

    def attach_window(self, window: Any) -> bool:
        """Attach the single floating orb PyWebView window (refuses duplicate attachment)."""
        with self._lock:
            if self._window is not None and not self._destroyed:
                return False
            self._window = window
            self._destroyed = False
            return True

    def create_orb_window(
        self,
        webview_module: Any,
        html_content: str,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Create the single frameless, transparent, always-on-top, no-focus orb window."""
        with self._lock:
            if self._window is not None and not self._destroyed:
                return self._window

            cfg = settings or {}
            corner = str(cfg.get("floating_orb_corner", "bottom_right"))
            custom_x = cfg.get("floating_orb_x")
            custom_y = cfg.get("floating_orb_y")
            w, h = ORB_COLLAPSED_SIZE
            x, y = compute_orb_position(
                corner=corner,
                width=w,
                height=h,
                custom_x=custom_x,
                custom_y=custom_y,
            )
            self._last_position = (x, y)

            win = webview_module.create_window(
                title=self.title,
                html=html_content,
                js_api=None,
                width=w,
                height=h,
                x=x,
                y=y,
                min_size=(72, 72),
                resizable=False,
                hidden=True,
                frameless=True,
                easy_drag=True,
                shadow=False,
                focus=False,
                on_top=True,
                transparent=True,
                background_color="#000000",
                text_select=False,
            )
            self._window = win
            self._destroyed = False
            return win

    def load_url(self, url: str) -> None:
        with self._lock:
            win = self._window
        if win is not None and hasattr(win, "load_url"):
            try:
                win.load_url(url)
            except Exception as err:
                logger.debug("Failed to load orb URL %s: %s", url, err)

    def _apply_geometry(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            win = self._window
            expanded = self._panel_expanded
        if win is None:
            return

        w, h = ORB_EXPANDED_SIZE if expanded else ORB_COLLAPSED_SIZE
        corner = str(settings.get("floating_orb_corner", "bottom_right"))
        custom_x = settings.get("floating_orb_x")
        custom_y = settings.get("floating_orb_y")
        x, y = compute_orb_position(
            corner=corner,
            width=w,
            height=h,
            custom_x=custom_x,
            custom_y=custom_y,
        )

        resize_fn = getattr(win, "resize", None)
        if callable(resize_fn):
            try:
                resize_fn(w, h)
            except Exception:
                pass
        move_fn = getattr(win, "move", None)
        if callable(move_fn):
            try:
                move_fn(x, y)
            except Exception:
                pass
        with self._lock:
            self._last_position = (x, y)

    def _show_no_focus(self) -> None:
        with self._lock:
            win = self._window
        if win is None:
            return
        hwnd = find_orb_window_hwnd(self.title)
        if hwnd and show_window_no_activate(hwnd):
            return
        show_fn = getattr(win, "show", None)
        if callable(show_fn):
            try:
                show_fn()
            except Exception:
                pass
        if hwnd:
            apply_no_activate_topmost_style(hwnd)

    def _hide_window(self) -> None:
        with self._lock:
            win = self._window
        if win is None:
            return
        hide_fn = getattr(win, "hide", None)
        if callable(hide_fn):
            try:
                hide_fn()
            except Exception:
                pass

    def should_display(self, phase: str, is_active: bool, settings: Dict[str, Any]) -> bool:
        """Determine whether the floating orb window should currently be visible."""
        enabled = bool(settings.get("floating_orb_enabled", True))
        user_visible = bool(settings.get("floating_orb_visible", True))
        show_when_idle = bool(settings.get("floating_orb_show_when_idle", False))
        if not enabled or not user_visible:
            return False
        if is_active or phase in ("starting", "listening", "transcribing", "processing", "planning", "executing", "speaking", "error"):
            return True
        return show_when_idle

    def sync_state(
        self,
        phase: str,
        is_active: bool,
        settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Synchronize floating orb visibility and position with live VoiceService state."""
        cfg = settings or {}
        target_visible = self.should_display(phase, is_active, cfg)

        with self._lock:
            if self._destroyed:
                return False
            self._last_phase = phase
            self._last_is_active = is_active
            if not target_visible:
                self._panel_expanded = False
            was_visible = self._is_visible
            self._is_visible = target_visible
            win = self._window

        if win is None:
            return target_visible

        if target_visible:
            self._apply_geometry(cfg)
            if not was_visible:
                self._show_no_focus()
        elif was_visible:
            self._hide_window()

        return target_visible

    def set_panel_expanded(self, expanded: bool, settings: Optional[Dict[str, Any]] = None) -> bool:
        """Expand or collapse the compact orb status panel."""
        cfg = settings or {}
        with self._lock:
            self._panel_expanded = bool(expanded)
            visible = self._is_visible
        if visible:
            self._apply_geometry(cfg)
        return bool(expanded)

    def destroy(self) -> None:
        """Cleanly destroy the floating orb window on application shutdown."""
        with self._lock:
            win = self._window
            self._window = None
            self._is_visible = False
            self._panel_expanded = False
            self._destroyed = True
        if win is not None:
            destroy_fn = getattr(win, "destroy", None)
            if callable(destroy_fn):
                try:
                    destroy_fn()
                except Exception:
                    pass
