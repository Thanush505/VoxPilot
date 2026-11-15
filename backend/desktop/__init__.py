"""
VoxPilot - Native Windows Desktop Shell Subsystem
Provides the PyWebView (Microsoft Edge WebView2) desktop container, single-instance
Windows Named Mutex guard, managed in-process Uvicorn server lifecycle, and Win32
window focus/branding integration.
"""

from backend.desktop.shell import (
    DEFAULT_MUTEX_NAME,
    ALLOWED_LOOPBACK_HOSTS,
    DesktopShellController,
    ManagedUvicornServer,
    NullTextStream,
    SingleInstanceGuard,
    apply_window_icon,
    build_startup_error_html,
    build_startup_loading_html,
    configure_windows_desktop_process,
    ensure_safe_standard_streams,
    focus_existing_voxpilot_window,
    focus_window_by_title,
    get_shared_desktop_controller,
    set_shared_desktop_controller,
    validate_loopback_host,
)

__all__ = [
    "DEFAULT_MUTEX_NAME",
    "ALLOWED_LOOPBACK_HOSTS",
    "DesktopShellController",
    "ManagedUvicornServer",
    "NullTextStream",
    "SingleInstanceGuard",
    "apply_window_icon",
    "build_startup_error_html",
    "build_startup_loading_html",
    "configure_windows_desktop_process",
    "ensure_safe_standard_streams",
    "focus_existing_voxpilot_window",
    "focus_window_by_title",
    "get_shared_desktop_controller",
    "set_shared_desktop_controller",
    "validate_loopback_host",
]
