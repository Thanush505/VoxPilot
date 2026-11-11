"""
VoxPilot - Windows Window Manager Subsystem
Provides safe window discovery, identification, validation, and management for Windows 11.
Enforces strict allowlist validation against approved applications before every window operation.
"""

from __future__ import annotations

import os
import re
import sys
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

import ctypes

try:
    import win32api
    import win32gui
    import win32con
    import win32process
    import win32service
except ImportError:
    win32api = None  # type: ignore
    win32gui = None  # type: ignore
    win32con = None  # type: ignore
    win32process = None  # type: ignore
    win32service = None  # type: ignore

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

from backend.automation.application_launcher import (
    ApplicationConfig,
    ApplicationRegistry,
    ApplicationNotAllowedError,
    DEFAULT_APPROVED_APPLICATIONS,
    is_path_in_approved_directories,
)

logger = logging.getLogger(__name__)

# Canonical UWP/MSIX package family name for Windows 11 Calculator
EXPECTED_CALCULATOR_PACKAGE_FAMILY = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"


def get_process_package_family_name(pid: int) -> Optional[str]:
    """Retrieve the UWP/MSIX Package Family Name for a process ID on Windows.

    Returns:
        Optional[str]: Package Family Name (e.g. 'Microsoft.WindowsCalculator_8wekyb3d8bbwe')
                       or None if not packaged, unsupported, or inaccessible.
    """
    if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "kernel32"):
        return None

    kernel32 = ctypes.windll.kernel32
    if not hasattr(kernel32, "GetPackageFamilyName"):
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h_proc = None
    try:
        from ctypes import wintypes
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_proc:
            return None

        length = wintypes.UINT(0)
        # First call retrieves required length in chars (returns ERROR_INSUFFICIENT_BUFFER = 122)
        res = kernel32.GetPackageFamilyName(h_proc, ctypes.byref(length), None)
        if res == 122 and length.value > 0:
            buf = ctypes.create_unicode_buffer(length.value)
            res = kernel32.GetPackageFamilyName(h_proc, ctypes.byref(length), buf)
            if res == 0:
                return buf.value
        return None
    except Exception as err:
        logger.debug(f"Failed to query package family name for PID {pid}: {err}")
        return None
    finally:
        if h_proc and hasattr(kernel32, "CloseHandle"):
            try:
                kernel32.CloseHandle(h_proc)
            except Exception:
                pass


class WindowManagerError(Exception):
    """Base exception for window management errors."""
    pass


class WindowNotFoundError(WindowManagerError):
    """Raised when a window with the given HWND or application query cannot be found."""
    pass


class WindowNotAllowedError(WindowManagerError):
    """Raised when a window does not belong to an approved application."""
    pass


class WindowOperationError(WindowManagerError):
    """Raised when a window management operation fails."""
    pass


class WindowAmbiguityError(WindowManagerError):
    """Raised when multiple windows match an ambiguous query that requires a unique target."""

    def __init__(self, message: str, matching_windows: Optional[List["WindowInfo"]] = None):
        super().__init__(message)
        self.matching_windows: List["WindowInfo"] = matching_windows or []


@dataclass(frozen=True)
class WindowInfo:
    """Immutable metadata model representing a discovered top-level window."""
    hwnd: int
    title: str
    app_id: str
    display_name: str
    pid: int
    process_name: str
    executable_path: str
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    is_responsive: bool
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)

    def to_dict(self) -> Dict[str, Any]:
        """Convert window metadata to a dictionary representation."""
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "app_id": self.app_id,
            "display_name": self.display_name,
            "pid": self.pid,
            "process_name": self.process_name,
            "executable_path": self.executable_path,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
            "is_responsive": self.is_responsive,
            "rect": {
                "left": self.rect[0],
                "top": self.rect[1],
                "right": self.rect[2],
                "bottom": self.rect[3],
            },
        }


class WindowManager:
    """Discovers, validates, and manipulates windows for approved Windows applications."""

    def __init__(self, registry: Optional[ApplicationRegistry] = None):
        self._registry = registry if registry is not None else ApplicationRegistry()

    def _ensure_desktop(self) -> None:
        """Attach thread to active user desktop if available.

        In background worker shells or subshell environments on Windows, the thread desktop
        may default to an isolated desktop object. Attaching to 'Default' enables enumerating
        and interacting with interactive user windows.
        """
        if win32service is not None and win32con is not None:
            try:
                hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
                hdesk.SetThreadDesktop()
            except Exception as err:
                logger.debug(f"Could not switch thread desktop to Default: {err}")

    def _get_top_level_hwnds(self) -> List[int]:
        """Enumerate top-level window handles in z-order (top to bottom)."""
        if win32gui is None:
            logger.warning("win32gui is unavailable; cannot enumerate windows.")
            return []

        hwnds: List[int] = []

        def enum_callback(hwnd: int, _: Any) -> bool:
            hwnds.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(enum_callback, 0)
        except Exception as err:
            logger.debug(f"win32gui.EnumWindows error: {err}")

        # If EnumWindows returned no windows (e.g. running in secondary desktop context), try desktop fallback
        if not hwnds and win32service is not None and win32con is not None:
            try:
                hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
                try:
                    desktop_handles = hdesk.EnumDesktopWindows()
                    hwnds = [int(h) for h in desktop_handles]
                finally:
                    hdesk.CloseDesktop()
            except Exception as desk_err:
                logger.debug(f"Desktop window enumeration fallback failed: {desk_err}")

        return hwnds

    def is_window_visible(self, hwnd: int) -> bool:
        """Check whether a window handle exists and is currently visible."""
        if win32gui is None or not win32gui.IsWindow(hwnd):
            return False
        return bool(win32gui.IsWindowVisible(hwnd))

    def is_window_responsive(self, hwnd: int) -> bool:
        """Check whether the target window's message loop is responsive.

        Uses non-blocking user32 IsHungAppWindow API.
        """
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            return True

        try:
            # IsHungAppWindow returns non-zero (True) if the application is hung
            is_hung = ctypes.windll.user32.IsHungAppWindow(hwnd)
            return not bool(is_hung)
        except Exception as err:
            logger.debug(f"IsHungAppWindow check failed for HWND {hwnd}: {err}")
            return True

    def _verify_calculator_hosted_window(self, hwnd: int) -> Optional[Tuple[int, Path]]:
        """Verify that an ApplicationFrameHost window actually hosts genuine Microsoft Windows Calculator.

        Enforces:
        1. Enumerates child windows belonging to the top-level window.
        2. Discovers child windows with class 'Windows.UI.Core.CoreWindow'.
        3. Retrieves the child window's PID via win32process.GetWindowThreadProcessId.
        4. Verifies the child process executable name is 'CalculatorApp.exe'.
        5. Verifies canonical path is within approved directories for Calculator (e.g. WindowsApps).
        6. Verifies UWP package family name matches EXPECTED_CALCULATOR_PACKAGE_FAMILY ('Microsoft.WindowsCalculator_8wekyb3d8bbwe') exactly.
        7. Fail-closed: rejects missing, empty, or unresolvable package identities with zero fallback.
        8. Rejects ambiguous multiple different package hosts or inaccessible processes.

        Fail-closed: Never relies on window titles. If child identity cannot be verified, returns None.

        Returns:
            Optional[Tuple[int, Path]]: (child_pid, child_exe_path) if genuine Calculator, else None.
        """
        if win32gui is None or win32process is None or psutil is None:
            return None

        children: List[int] = []

        def enum_child_callback(child_hwnd: int, _: Any) -> bool:
            children.append(child_hwnd)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, enum_child_callback, 0)
        except Exception as err:
            logger.debug(f"EnumChildWindows failed on HWND {hwnd}: {err}")
            return None

        calculator_candidates: List[Tuple[int, Path]] = []
        calc_config = self._registry._applications.get("calculator")
        approved_dirs = calc_config.approved_directories if calc_config else []

        for ch in children:
            try:
                ch_class = win32gui.GetClassName(ch)
            except Exception:
                continue

            # Hosted UWP application content is hosted in a CoreWindow
            if ch_class == "Windows.UI.Core.CoreWindow":
                try:
                    _, ch_pid = win32process.GetWindowThreadProcessId(ch)
                except Exception:
                    continue

                if ch_pid <= 0:
                    continue

                try:
                    proc = psutil.Process(ch_pid)
                    pname = proc.name().lower()
                    raw_exe = proc.exe()
                    if not raw_exe:
                        continue
                    exe_path = Path(raw_exe).resolve()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

                # Must match CalculatorApp.exe
                if pname != "calculatorapp.exe":
                    continue

                # Must reside in approved directory (e.g. Program Files\WindowsApps)
                if not is_path_in_approved_directories(exe_path, approved_dirs):
                    logger.warning(f"CalculatorApp.exe at '{exe_path}' is outside approved directories.")
                    continue

                # Mandatory Exact Package Family Name verification (fail-closed)
                try:
                    pkg_family = get_process_package_family_name(ch_pid)
                except Exception as pkg_err:
                    logger.warning(f"Failed to query package family name for PID {ch_pid}: {pkg_err}")
                    continue

                if not pkg_family or pkg_family.strip().lower() != EXPECTED_CALCULATOR_PACKAGE_FAMILY.lower():
                    logger.warning(
                        f"Rejected CalculatorApp candidate PID {ch_pid}: package family '{pkg_family}' "
                        f"does not match expected '{EXPECTED_CALCULATOR_PACKAGE_FAMILY}'."
                    )
                    continue

                calculator_candidates.append((ch_pid, exe_path))

        # Must have found genuine CalculatorApp child relationship without ambiguity
        if calculator_candidates:
            # Deduplicate by PID
            unique_candidates = {cand[0]: cand for cand in calculator_candidates}
            if len(unique_candidates) == 1:
                return next(iter(unique_candidates.values()))
            else:
                logger.warning(f"Ambiguous multiple CalculatorApp processes found for HWND {hwnd}.")
                return None

        return None

    def validate_window_ownership(self, hwnd: int) -> Tuple[str, str, int, str, str]:
        """Verify that a window exists, is valid, and belongs to an approved application.

        Checks:
        1. Window handle is a valid, existing window.
        2. Process ID is non-zero and associated with an accessible running process.
        3. Executable path resides within an approved directory for the application.
        4. Executable filename matches the approved binary name.
        5. Handles Windows 11 packaged applications (e.g. Calculator under ApplicationFrameHost.exe)
           by inspecting child CoreWindow and verifying CalculatorApp.exe package identity.

        Returns:
            Tuple[str, str, int, str, str]: (app_id, display_name, pid, process_name, executable_path)

        Raises:
            WindowNotFoundError: If the HWND is not a valid window.
            WindowNotAllowedError: If the window belongs to an unapproved application or untrusted path.
        """
        if win32gui is None or win32process is None or psutil is None:
            raise WindowManagerError("Required OS libraries (win32gui, win32process, psutil) are not available.")

        if not win32gui.IsWindow(hwnd):
            raise WindowNotFoundError(f"Window handle {hwnd} is not a valid or existing window.")

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception as err:
            raise WindowNotFoundError(f"Could not retrieve process ID for window {hwnd}: {err}")

        if pid == 0:
            raise WindowNotAllowedError(f"Window handle {hwnd} is owned by system PID 0.")

        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()
            raw_exe = proc.exe()
            if not raw_exe:
                raise WindowNotAllowedError(f"Process {pid} has an empty executable path.")
            exe_path = Path(raw_exe).resolve()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as proc_err:
            raise WindowNotFoundError(f"Process {pid} for window {hwnd} is not accessible: {proc_err}")

        norm_proc_name = proc_name.lower()
        title = win32gui.GetWindowText(hwnd) or ""

        # Check against approved applications
        for app_id, config in self._registry._applications.items():
            expected_names = [cand.name.lower() for cand in config.executable_candidates]
            if config.registry_app_path:
                expected_names.append(Path(config.registry_app_path).name.lower())

            # 1. Standard application match
            if norm_proc_name in expected_names:
                if is_path_in_approved_directories(exe_path, config.approved_directories):
                    return (config.app_id, config.display_name, pid, proc_name, str(exe_path))

            # 2. Windows 11 Packaged Apps (e.g. Calculator under ApplicationFrameHost.exe)
            if app_id == "calculator" and norm_proc_name == "applicationframehost.exe":
                try:
                    cls_name = win32gui.GetClassName(hwnd)
                except Exception:
                    cls_name = ""

                # Enforce that class is ApplicationFrameWindow
                # And rigorously verify the hosted child window / process identity (fail closed)
                if cls_name == "ApplicationFrameWindow":
                    calc_hosted = self._verify_calculator_hosted_window(hwnd)
                    if calc_hosted is not None:
                        child_pid, child_exe = calc_hosted
                        return (config.app_id, config.display_name, child_pid, "CalculatorApp.exe", str(child_exe))

        raise WindowNotAllowedError(
            f"Window {hwnd} (PID {pid}, '{proc_name}', '{exe_path}') does not belong to an approved application."
        )

    def get_window_info(self, hwnd: int) -> WindowInfo:
        """Retrieve complete, validated WindowInfo for a window handle.

        Raises:
            WindowNotFoundError: If window does not exist.
            WindowNotAllowedError: If window is not an approved application.
        """
        app_id, display_name, pid, proc_name, exe_path = self.validate_window_ownership(hwnd)

        title = win32gui.GetWindowText(hwnd) if win32gui is not None else ""
        is_visible = self.is_window_visible(hwnd)
        is_responsive = self.is_window_responsive(hwnd)

        # Minimized / Maximized state checks
        is_minimized = False
        is_maximized = False
        if win32gui is not None:
            try:
                is_minimized = bool(win32gui.IsIconic(hwnd))
            except Exception:
                pass

        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            try:
                is_maximized = bool(ctypes.windll.user32.IsZoomed(hwnd))
            except Exception:
                pass

        # Window Rect
        rect = (0, 0, 0, 0)
        if win32gui is not None:
            try:
                rect = win32gui.GetWindowRect(hwnd)
            except Exception:
                pass

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            app_id=app_id,
            display_name=display_name,
            pid=pid,
            process_name=proc_name,
            executable_path=exe_path,
            is_visible=is_visible,
            is_minimized=is_minimized,
            is_maximized=is_maximized,
            is_responsive=is_responsive,
            rect=rect,
        )

    def find_windows(
        self,
        app_name: Optional[str] = None,
        title_pattern: Optional[str] = None,
        visible_only: bool = True,
    ) -> List[WindowInfo]:
        """Discover top-level windows matching application identity, title, or visibility filters.

        Args:
            app_name: Optional application name or alias (e.g. 'Notepad', 'Chrome', 'calc').
                      If provided, must resolve against the approved application allowlist.
            title_pattern: Optional regex or substring filter against the window title.
            visible_only: Whether to filter out hidden/background windows (default True).

        Returns:
            List[WindowInfo]: Matching windows ordered in top-to-bottom z-order.

        Raises:
            ApplicationNotAllowedError: If app_name is provided but not in the approved allowlist.
        """
        target_app_id: Optional[str] = None
        if app_name is not None:
            # Resolves query or raises ApplicationNotAllowedError
            resolved_config = self._registry.resolve(app_name)
            target_app_id = resolved_config.app_id

        regex: Optional[re.Pattern] = None
        if title_pattern:
            try:
                regex = re.compile(title_pattern, re.IGNORECASE)
            except re.error:
                # Fallback to escaped literal match if title_pattern contains invalid regex characters
                regex = re.compile(re.escape(title_pattern), re.IGNORECASE)

        hwnds = self._get_top_level_hwnds()
        results: List[WindowInfo] = []

        for hwnd in hwnds:
            # Visibility filtering
            if visible_only and not self.is_window_visible(hwnd):
                continue

            # Ownership validation
            try:
                info = self.get_window_info(hwnd)
            except (WindowNotFoundError, WindowNotAllowedError, WindowManagerError):
                continue

            # App ID filter
            if target_app_id is not None and info.app_id != target_app_id:
                continue

            # Skip helper/offscreen worker windows with empty titles when visible_only
            if visible_only and not info.title.strip():
                continue

            # Title pattern filter
            if regex is not None and not regex.search(info.title):
                continue

            results.append(info)

        return results

    def activate_window(self, hwnd: int) -> bool:
        """Bring an approved application window to the foreground and activate it.

        Enforces allowlist ownership re-validation immediately before execution.
        If the window is minimized, restores it prior to foreground activation.
        Uses standard SetForegroundWindow without focus-stealing thread attachment hacks.

        Returns:
            bool: True if the window was activated and confirmed in the foreground.

        Raises:
            WindowNotFoundError: If window does not exist.
            WindowNotAllowedError: If window is not an approved application.
            WindowOperationError: If the window is hung or OS activation fails.
        """
        # Re-validate ownership immediately before operating
        self.validate_window_ownership(hwnd)

        if not self.is_window_responsive(hwnd):
            raise WindowOperationError(f"Window {hwnd} is unresponsive/hung and cannot be activated.")

        if win32gui is None or win32con is None:
            raise WindowManagerError("win32gui is unavailable.")

        try:
            # If minimized, restore it first
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.05)

            # Standard Windows foreground activation without focus-stealing thread attachment
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as fg_err:
                logger.debug(f"win32gui.SetForegroundWindow failed for HWND {hwnd}: {fg_err}")
                return False

            # Verification
            time.sleep(0.05)
            new_fg = win32gui.GetForegroundWindow()
            return new_fg == hwnd

        except Exception as err:
            raise WindowOperationError(f"Failed to activate window {hwnd}: {err}")

    def minimize_window(self, hwnd: int) -> bool:
        """Minimize an approved application window.

        Enforces allowlist ownership re-validation immediately before execution.

        Returns:
            bool: True if the window is successfully minimized.

        Raises:
            WindowNotFoundError: If window does not exist.
            WindowNotAllowedError: If window is not an approved application.
            WindowOperationError: If minimization fails.
        """
        self.validate_window_ownership(hwnd)

        if win32gui is None or win32con is None:
            raise WindowManagerError("win32gui is unavailable.")

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            time.sleep(0.05)
            return bool(win32gui.IsIconic(hwnd))
        except Exception as err:
            raise WindowOperationError(f"Failed to minimize window {hwnd}: {err}")

    def maximize_window(self, hwnd: int) -> bool:
        """Maximize an approved application window.

        Enforces allowlist ownership re-validation immediately before execution.

        Returns:
            bool: True if the window is successfully maximized.

        Raises:
            WindowNotFoundError: If window does not exist.
            WindowNotAllowedError: If window is not an approved application.
            WindowOperationError: If maximization fails.
        """
        self.validate_window_ownership(hwnd)

        if win32gui is None or win32con is None:
            raise WindowManagerError("win32gui is unavailable.")

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            time.sleep(0.05)
            if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
                return bool(ctypes.windll.user32.IsZoomed(hwnd))
            return True
        except Exception as err:
            raise WindowOperationError(f"Failed to maximize window {hwnd}: {err}")

    def restore_window(self, hwnd: int) -> bool:
        """Restore an approved application window to its normal (unminimized, unmaximized) state.

        Enforces allowlist ownership re-validation immediately before execution.

        Returns:
            bool: True if the window is successfully restored.

        Raises:
            WindowNotFoundError: If window does not exist.
            WindowNotAllowedError: If window is not an approved application.
            WindowOperationError: If restoration fails.
        """
        self.validate_window_ownership(hwnd)

        if win32gui is None or win32con is None:
            raise WindowManagerError("win32gui is unavailable.")

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)
            is_iconic = bool(win32gui.IsIconic(hwnd))
            is_zoomed = False
            if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
                is_zoomed = bool(ctypes.windll.user32.IsZoomed(hwnd))
            return (not is_iconic) and (not is_zoomed)
        except Exception as err:
            raise WindowOperationError(f"Failed to restore window {hwnd}: {err}")

    def manage_window(
        self,
        action: str,
        hwnd: Optional[int] = None,
        app_name: Optional[str] = None,
    ) -> WindowInfo:
        """High-level window management dispatcher supporting action dispatch and ambiguity detection.

        Args:
            action: Window action to execute ('activate', 'minimize', 'maximize', 'restore').
            hwnd: Optional explicit window handle.
            app_name: Optional application name used when hwnd is not supplied.

        Returns:
            WindowInfo: Updated metadata for the managed window.

        Raises:
            WindowAmbiguityError: When app_name resolves to multiple windows and no hwnd is provided.
            WindowNotFoundError: When no matching window is found.
            WindowNotAllowedError: When the target is not approved.
            WindowManagerError: On invalid arguments or unsupported actions.
        """
        norm_action = action.strip().lower()
        if norm_action not in {"activate", "minimize", "maximize", "restore"}:
            raise WindowManagerError(f"Unsupported window action: '{action}'.")

        target_hwnd = hwnd
        if target_hwnd is None:
            if not app_name:
                raise WindowManagerError("Either hwnd or app_name must be provided to manage_window.")

            # Search for visible windows matching app_name first, then all if none visible
            matching = self.find_windows(app_name=app_name, visible_only=True)
            if not matching:
                matching = self.find_windows(app_name=app_name, visible_only=False)

            if not matching:
                raise WindowNotFoundError(f"No windows found for application '{app_name}'.")

            if len(matching) > 1:
                raise WindowAmbiguityError(
                    f"Multiple windows ({len(matching)}) found for '{app_name}'. Please specify an explicit hwnd.",
                    matching_windows=matching,
                )

            target_hwnd = matching[0].hwnd
        else:
            # HWND is provided: if app_name is also provided, validate that the HWND actually belongs to app_name
            if app_name is not None:
                expected_config = self._registry.resolve(app_name)
                window_info = self.get_window_info(target_hwnd)
                if window_info.app_id != expected_config.app_id:
                    raise WindowNotAllowedError(
                        f"Window {target_hwnd} belongs to '{window_info.display_name}' ({window_info.app_id}), "
                        f"not the requested application '{expected_config.display_name}' ({expected_config.app_id})."
                    )

        # Dispatch action
        if norm_action == "activate":
            activated = self.activate_window(target_hwnd)
            if not activated:
                raise WindowOperationError(
                    f"Windows did not allow foreground activation for window {target_hwnd}."
                )
        elif norm_action == "minimize":
            self.minimize_window(target_hwnd)
        elif norm_action == "maximize":
            self.maximize_window(target_hwnd)
        elif norm_action == "restore":
            self.restore_window(target_hwnd)

        return self.get_window_info(target_hwnd)
