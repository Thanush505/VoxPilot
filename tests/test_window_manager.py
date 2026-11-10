"""
VoxPilot - Window Manager Subsystem Unit and Integration Tests
Tests window discovery, allowlist validation, state inspection, multi-window ambiguity,
management operations (activate, minimize, maximize, restore), tool schemas, and opt-in live test.
"""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import FrozenInstanceError

from backend.automation.application_launcher import (
    ApplicationConfig,
    ApplicationRegistry,
    ApplicationNotAllowedError,
    DEFAULT_APPROVED_APPLICATIONS,
)
from backend.automation.window_manager import (
    EXPECTED_CALCULATOR_PACKAGE_FAMILY,
    WindowInfo,
    WindowManager,
    WindowManagerError,
    WindowNotFoundError,
    WindowNotAllowedError,
    WindowOperationError,
    WindowAmbiguityError,
)
from backend.tools.windows import (
    FindWindowsInput,
    FindWindowsResult,
    ManageWindowInput,
    ManageWindowResult,
    find_windows_tool,
    manage_window_tool,
)


# ==============================================================================
# 1. WindowInfo Data Model Tests
# ==============================================================================

def test_window_info_immutability():
    """WindowInfo must be an immutable frozen dataclass."""
    info = WindowInfo(
        hwnd=12345,
        title="Test Window",
        app_id="notepad",
        display_name="Notepad",
        pid=999,
        process_name="notepad.exe",
        executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True,
        is_minimized=False,
        is_maximized=False,
        is_responsive=True,
        rect=(0, 0, 800, 600),
    )
    with pytest.raises(FrozenInstanceError):
        info.title = "Modified Title"  # type: ignore


def test_window_info_to_dict_structure():
    """WindowInfo.to_dict must produce the complete expected dictionary representation."""
    info = WindowInfo(
        hwnd=1001,
        title="Untitled - Notepad",
        app_id="notepad",
        display_name="Notepad",
        pid=555,
        process_name="notepad.exe",
        executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True,
        is_minimized=False,
        is_maximized=False,
        is_responsive=True,
        rect=(10, 20, 810, 620),
    )
    data = info.to_dict()
    assert data["hwnd"] == 1001
    assert data["title"] == "Untitled - Notepad"
    assert data["app_id"] == "notepad"
    assert data["display_name"] == "Notepad"
    assert data["pid"] == 555
    assert data["process_name"] == "notepad.exe"
    assert data["executable_path"] == r"C:\Windows\System32\notepad.exe"
    assert data["is_visible"] is True
    assert data["is_minimized"] is False
    assert data["is_maximized"] is False
    assert data["is_responsive"] is True
    assert data["rect"] == {"left": 10, "top": 20, "right": 810, "bottom": 620}


# ==============================================================================
# 2. WindowManager Initialization Tests
# ==============================================================================

def test_window_manager_init_default_registry():
    """WindowManager defaults to loading DEFAULT_APPROVED_APPLICATIONS."""
    wm = WindowManager()
    assert "notepad" in wm._registry._applications
    assert "calculator" in wm._registry._applications
    assert "chrome" in wm._registry._applications
    assert "vscode" in wm._registry._applications


def test_window_manager_init_custom_registry():
    """WindowManager accepts a custom ApplicationRegistry."""
    custom_registry = ApplicationRegistry(
        applications={
            "notepad": DEFAULT_APPROVED_APPLICATIONS["notepad"]
        }
    )
    wm = WindowManager(registry=custom_registry)
    assert len(wm._registry._applications) == 1
    assert "notepad" in wm._registry._applications
    assert "chrome" not in wm._registry._applications


# ==============================================================================
# 3. Allowlist Ownership Validation Tests
# ==============================================================================

@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_notepad_success(mock_is_window, mock_get_pid, mock_proc):
    """Approved Notepad window passes ownership validation."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 1234)

    proc_instance = MagicMock()
    proc_instance.name.return_value = "notepad.exe"
    proc_instance.exe.return_value = r"C:\Windows\System32\notepad.exe"
    mock_proc.return_value = proc_instance

    wm = WindowManager()
    app_id, display_name, pid, proc_name, exe_path = wm.validate_window_ownership(1001)

    assert app_id == "notepad"
    assert display_name == "Notepad"
    assert pid == 1234
    assert proc_name == "notepad.exe"
    assert exe_path == str(Path(r"C:\Windows\System32\notepad.exe").resolve())


@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_chrome_success(mock_is_window, mock_get_pid, mock_proc):
    """Approved Chrome window passes ownership validation."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 2345)

    proc_instance = MagicMock()
    proc_instance.name.return_value = "chrome.exe"
    proc_instance.exe.return_value = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    mock_proc.return_value = proc_instance

    wm = WindowManager()
    app_id, display_name, pid, proc_name, _ = wm.validate_window_ownership(1002)

    assert app_id == "chrome"
    assert display_name == "Google Chrome"
    assert pid == 2345


@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_vscode_success(mock_is_window, mock_get_pid, mock_proc):
    """Approved VS Code window passes ownership validation."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 3456)

    proc_instance = MagicMock()
    proc_instance.name.return_value = "Code.exe"
    proc_instance.exe.return_value = r"C:\Program Files\Microsoft VS Code\Code.exe"
    mock_proc.return_value = proc_instance

    wm = WindowManager()
    app_id, display_name, pid, proc_name, _ = wm.validate_window_ownership(1003)

    assert app_id == "vscode"
    assert display_name == "Visual Studio Code"


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_calculator_uwp_success(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """Windows 11 Calculator hosted in ApplicationFrameHost.exe passes validation via child CoreWindow."""
    mock_is_window.return_value = True

    # 1004 is top-level ApplicationFrameWindow (PID 4567)
    # 9001 is child Windows.UI.Core.CoreWindow (PID 8888)
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"

    def enum_cb(h, cb, _):
        if h == 1004:
            cb(9001, None)
        return True
    mock_enum_child.side_effect = enum_cb

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"

    wm = WindowManager()
    app_id, display_name, pid, proc_name, _ = wm.validate_window_ownership(1004)

    assert app_id == "calculator"
    assert display_name == "Calculator"
    assert pid == 8888
    assert proc_name == "CalculatorApp.exe"


# ==============================================================================
# SEC-004 Regression Tests: Rigorous Calculator Package Identity
# ==============================================================================

@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.win32gui.GetWindowText")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_photos_with_calc_title_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_text, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Photos window titled 'calc_notes.jpg' hosted under ApplicationFrameHost is rejected."""
    mock_is_window.return_value = True
    mock_get_text.return_value = "calc_notes.jpg - Photos"
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 7777)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9002, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    photos_proc = MagicMock()
    photos_proc.name.return_value = "PhotosApp.exe"
    photos_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.Windows.Photos_12345\PhotosApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else photos_proc
    mock_pfn.return_value = "Microsoft.Windows.Photos_8wekyb3d8bbwe"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError, match="does not belong to an approved application"):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_unrelated_packaged_app_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: An unapproved UWP app hosted in ApplicationFrameHost is rejected."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 6666)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9003, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    fake_proc = MagicMock()
    fake_proc.name.return_value = "FakeCalc.exe"
    fake_proc.exe.return_value = r"C:\Program Files\WindowsApps\FakeCalc_123\FakeCalc.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else fake_proc
    mock_pfn.return_value = "FakeCalc_8wekyb3d8bbwe"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)

@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_missing_or_inaccessible_child_process_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child
):
    """SEC-004: Inaccessible or missing child processes under ApplicationFrameHost are rejected (fail-closed)."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 9999)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9004, None)

    import psutil
    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    def proc_error(pid):
        if pid == 4567:
            return host_proc
        raise psutil.AccessDenied(pid=pid)

    mock_proc.side_effect = proc_error

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_calculator_outside_approved_directory_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: CalculatorApp.exe located in an unapproved directory is rejected."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9005, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Users\Public\Downloads\CalculatorApp.exe"  # Untrusted!

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_untrusted_package_family_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: CalculatorApp binary with an untrusted package family is rejected."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = "MaliciousPackage_8wekyb3d8bbwe"  # Untrusted package!

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_ambiguous_child_windows_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Conflicting or ambiguous multiple child processes under ApplicationFrameHost are rejected."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else ((0, 8881) if h == 9001 else (0, 8882))
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"

    def enum_multiple(h, cb, _):
        cb(9001, None)
        cb(9002, None)
        return True
    mock_enum_child.side_effect = enum_multiple

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc1 = MagicMock()
    child_proc1.name.return_value = "CalculatorApp.exe"
    child_proc1.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_1\CalculatorApp.exe"

    child_proc2 = MagicMock()
    child_proc2.name.return_value = "CalculatorApp.exe"
    child_proc2.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_2\CalculatorApp.exe"

    def proc_dispatch(pid):
        if pid == 4567:
            return host_proc
        if pid == 8881:
            return child_proc1
        return child_proc2
    mock_proc.side_effect = proc_dispatch
    mock_pfn.return_value = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_exact_package_family_accepted(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Genuine Calculator with exact EXPECTED_CALCULATOR_PACKAGE_FAMILY is accepted."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9001, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = EXPECTED_CALCULATOR_PACKAGE_FAMILY

    wm = WindowManager()
    app_id, display_name, pid, proc_name, _ = wm.validate_window_ownership(1004)
    assert app_id == "calculator"
    assert display_name == "Calculator"
    assert pid == 8888
    assert proc_name == "CalculatorApp.exe"


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_prefix_match_with_different_publisher_id_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Package matching prefix 'Microsoft.WindowsCalculator_' but with wrong publisher ID is rejected."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__fakeid12345\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    # Matches prefix 'Microsoft.WindowsCalculator_' but possesses an unauthorized publisher ID
    mock_pfn.return_value = "Microsoft.WindowsCalculator_fakeid12345"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_different_package_family_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Different package family (e.g. Alarms & Clock) is rejected even if named CalculatorApp.exe."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsAlarms_8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = "Microsoft.WindowsAlarms_8wekyb3d8bbwe"

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_none_package_identity_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: None package identity causes fail-closed rejection without fallback."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = None  # Missing package identity!

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_empty_package_identity_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Empty string package identity causes fail-closed rejection."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = ""  # Empty string!

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_package_retrieval_exception_rejected(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Exception during package family retrieval causes safe fail-closed rejection."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.side_effect = OSError("Win32 RPC communication failure")

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.get_process_package_family_name")
@patch("backend.automation.window_manager.win32gui.EnumChildWindows")
@patch("backend.automation.window_manager.win32gui.GetClassName")
@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_sec004_no_fallback_to_filename_or_directory_when_package_unavailable(
    mock_is_window, mock_get_pid, mock_proc, mock_get_class, mock_enum_child, mock_pfn
):
    """SEC-004: Process named CalculatorApp.exe in approved directory is NEVER accepted if package identity is unavailable."""
    mock_is_window.return_value = True
    mock_get_pid.side_effect = lambda h: (0, 4567) if h == 1004 else (0, 8888)
    mock_get_class.side_effect = lambda h: "ApplicationFrameWindow" if h == 1004 else "Windows.UI.Core.CoreWindow"
    mock_enum_child.side_effect = lambda h, cb, _: cb(9006, None)

    host_proc = MagicMock()
    host_proc.name.return_value = "ApplicationFrameHost.exe"
    host_proc.exe.return_value = r"C:\Windows\System32\ApplicationFrameHost.exe"

    # Even though filename is CalculatorApp.exe and path is canonically in WindowsApps:
    child_proc = MagicMock()
    child_proc.name.return_value = "CalculatorApp.exe"
    child_proc.exe.return_value = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe\CalculatorApp.exe"

    mock_proc.side_effect = lambda pid: host_proc if pid == 4567 else child_proc
    mock_pfn.return_value = None  # Unavailable package identity

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1004)


@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_unapproved_process_rejected(mock_is_window, mock_get_pid, mock_proc):
    """Processes not on the allowlist (e.g. powershell.exe) are rejected with WindowNotAllowedError."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 9999)

    proc_instance = MagicMock()
    proc_instance.name.return_value = "powershell.exe"
    proc_instance.exe.return_value = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    mock_proc.return_value = proc_instance

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError, match="does not belong to an approved application"):
        wm.validate_window_ownership(1006)


@patch("backend.automation.window_manager.psutil.Process")
@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_approved_name_in_unapproved_directory_rejected(mock_is_window, mock_get_pid, mock_proc):
    """A binary named notepad.exe located in an unapproved directory is rejected."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 8888)

    proc_instance = MagicMock()
    proc_instance.name.return_value = "notepad.exe"
    proc_instance.exe.return_value = r"C:\Users\Public\Downloads\notepad.exe"
    mock_proc.return_value = proc_instance

    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError):
        wm.validate_window_ownership(1007)


@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_invalid_hwnd_rejected(mock_is_window):
    """Invalid or destroyed HWND raises WindowNotFoundError."""
    mock_is_window.return_value = False
    wm = WindowManager()
    with pytest.raises(WindowNotFoundError, match="not a valid or existing window"):
        wm.validate_window_ownership(999999)


@patch("backend.automation.window_manager.win32process.GetWindowThreadProcessId")
@patch("backend.automation.window_manager.win32gui.IsWindow")
def test_validate_ownership_pid_zero_rejected(mock_is_window, mock_get_pid):
    """HWND associated with PID 0 raises WindowNotAllowedError."""
    mock_is_window.return_value = True
    mock_get_pid.return_value = (0, 0)
    wm = WindowManager()
    with pytest.raises(WindowNotAllowedError, match="system PID 0"):
        wm.validate_window_ownership(1008)


# ==============================================================================
# 4. Window Enumeration and Discovery Tests
# ==============================================================================

@patch.object(WindowManager, "_get_top_level_hwnds")
@patch.object(WindowManager, "get_window_info")
@patch.object(WindowManager, "is_window_visible")
def test_find_windows_preserves_z_order(mock_is_visible, mock_get_info, mock_hwnds):
    """find_windows returns matching windows preserving top-to-bottom z-order."""
    mock_hwnds.return_value = [101, 102, 103]
    mock_is_visible.return_value = True

    info_101 = WindowInfo(
        hwnd=101, title="Document 1 - Notepad", app_id="notepad", display_name="Notepad",
        pid=1, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,100,100)
    )
    info_102 = WindowInfo(
        hwnd=102, title="Document 2 - Notepad", app_id="notepad", display_name="Notepad",
        pid=2, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(10,10,110,110)
    )
    info_103 = WindowInfo(
        hwnd=103, title="Google Chrome", app_id="chrome", display_name="Google Chrome",
        pid=3, process_name="chrome.exe", executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(20,20,120,120)
    )

    mock_get_info.side_effect = lambda h: {101: info_101, 102: info_102, 103: info_103}[h]

    wm = WindowManager()
    results = wm.find_windows(app_name="notepad")

    assert len(results) == 2
    assert results[0].hwnd == 101
    assert results[1].hwnd == 102


@patch.object(WindowManager, "_get_top_level_hwnds")
@patch.object(WindowManager, "is_window_visible")
def test_find_windows_visible_only_filtering(mock_is_visible, mock_hwnds):
    """find_windows filters out hidden windows when visible_only=True."""
    mock_hwnds.return_value = [201, 202]
    # 201 is visible, 202 is hidden
    mock_is_visible.side_effect = lambda h: h == 201

    with patch.object(WindowManager, "get_window_info") as mock_get_info:
        info_201 = WindowInfo(
            hwnd=201, title="Visible Notepad", app_id="notepad", display_name="Notepad",
            pid=1, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
            is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,100,100)
        )
        mock_get_info.return_value = info_201

        wm = WindowManager()
        results = wm.find_windows(app_name="notepad", visible_only=True)

        assert len(results) == 1
        assert results[0].hwnd == 201
        # get_window_info should only be called for visible HWND 201
        mock_get_info.assert_called_once_with(201)


@patch.object(WindowManager, "_get_top_level_hwnds")
@patch.object(WindowManager, "get_window_info")
@patch.object(WindowManager, "is_window_visible")
def test_find_windows_title_pattern_filter(mock_is_visible, mock_get_info, mock_hwnds):
    """find_windows filters by title pattern."""
    mock_hwnds.return_value = [301, 302]
    mock_is_visible.return_value = True

    info_budget = WindowInfo(
        hwnd=301, title="Budget_2026.txt - Notepad", app_id="notepad", display_name="Notepad",
        pid=1, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,100,100)
    )
    info_meeting = WindowInfo(
        hwnd=302, title="Meeting_Notes.txt - Notepad", app_id="notepad", display_name="Notepad",
        pid=2, process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(10,10,110,110)
    )
    mock_get_info.side_effect = lambda h: {301: info_budget, 302: info_meeting}[h]

    wm = WindowManager()
    results = wm.find_windows(app_name="notepad", title_pattern="budget")

    assert len(results) == 1
    assert results[0].hwnd == 301


def test_find_windows_unapproved_app_raises():
    """find_windows raises ApplicationNotAllowedError when querying unapproved applications."""
    wm = WindowManager()
    with pytest.raises(ApplicationNotAllowedError):
        wm.find_windows(app_name="cmd.exe")


# ==============================================================================
# 5. Window State Inspection Tests
# ==============================================================================

@patch("backend.automation.window_manager.ctypes.windll.user32.IsHungAppWindow")
def test_is_window_responsive(mock_is_hung):
    """is_window_responsive correctly interprets IsHungAppWindow."""
    wm = WindowManager()

    # IsHungAppWindow returns 0 -> window is responsive
    mock_is_hung.return_value = 0
    assert wm.is_window_responsive(1001) is True

    # IsHungAppWindow returns 1 -> window is hung
    mock_is_hung.return_value = 1
    assert wm.is_window_responsive(1001) is False


# ==============================================================================
# 6. Window Management Operations Tests
# ==============================================================================

@patch.object(WindowManager, "validate_window_ownership")
@patch.object(WindowManager, "is_window_responsive")
@patch("backend.automation.window_manager.win32gui.GetForegroundWindow")
@patch("backend.automation.window_manager.win32gui.SetForegroundWindow")
@patch("backend.automation.window_manager.win32gui.ShowWindow")
@patch("backend.automation.window_manager.win32gui.IsIconic")
def test_activate_window_success(
    mock_is_iconic, mock_show, mock_set_fg, mock_get_fg, mock_resp, mock_val
):
    """activate_window restores if iconic and brings window to foreground using standard API."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_resp.return_value = True
    mock_is_iconic.return_value = True  # was minimized
    mock_get_fg.return_value = 5001     # confirmed foreground

    wm = WindowManager()
    success = wm.activate_window(5001)

    assert success is True
    mock_show.assert_called_once()  # Restored
    mock_set_fg.assert_called_once_with(5001)


# ==============================================================================
# SEC-005 Regression Tests: Removal of AttachThreadInput and Focus Stealing
# ==============================================================================

@patch.object(WindowManager, "validate_window_ownership")
@patch.object(WindowManager, "is_window_responsive")
@patch("backend.automation.window_manager.win32gui.GetForegroundWindow")
@patch("backend.automation.window_manager.win32gui.SetForegroundWindow")
@patch("backend.automation.window_manager.win32gui.IsIconic", return_value=False)
def test_sec005_activate_window_refusal_returns_false(
    mock_is_iconic, mock_set_fg, mock_get_fg, mock_resp, mock_val
):
    """SEC-005: When Windows refuses activation (GetForegroundWindow != hwnd), activate_window returns False."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_resp.return_value = True
    mock_get_fg.return_value = 9999  # Another window remained foreground!

    wm = WindowManager()
    result = wm.activate_window(5001)

    assert result is False
    mock_set_fg.assert_called_once_with(5001)


@patch.object(WindowManager, "validate_window_ownership")
@patch.object(WindowManager, "is_window_responsive")
@patch("backend.automation.window_manager.win32gui.SetForegroundWindow")
@patch("backend.automation.window_manager.win32gui.IsIconic", return_value=False)
def test_sec005_activate_window_api_exception_returns_false(
    mock_is_iconic, mock_set_fg, mock_resp, mock_val
):
    """SEC-005: When SetForegroundWindow raises an OS exception, activate_window safely returns False."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_resp.return_value = True
    mock_set_fg.side_effect = OSError("Access denied by OS")

    wm = WindowManager()
    result = wm.activate_window(5001)

    assert result is False


def test_sec005_no_attach_thread_input_in_activate_window():
    """SEC-005: Verify AttachThreadInput and BringWindowToTop are completely absent from activate_window."""
    import inspect
    from backend.automation.window_manager import WindowManager

    source = inspect.getsource(WindowManager.activate_window)
    assert "AttachThreadInput" not in source, "AttachThreadInput must be completely removed"
    assert "BringWindowToTop" not in source, "BringWindowToTop must be completely removed"


# ==============================================================================
# SEC-006 Regression Tests: Truthful Activation Failure Reporting
# ==============================================================================

@patch.object(WindowManager, "activate_window", return_value=False)
@patch.object(WindowManager, "get_window_info")
def test_sec006_manage_window_activation_refusal_raises_operation_error(mock_info, mock_activate):
    """SEC-006: manage_window raises WindowOperationError when activate_window returns False."""
    wm = WindowManager()
    with pytest.raises(WindowOperationError, match="did not allow foreground activation"):
        wm.manage_window("activate", hwnd=1001)


@patch.object(WindowManager, "manage_window")
def test_sec006_manage_window_tool_reports_activation_restricted(mock_manage):
    """SEC-006: manage_window_tool returns success=False and status='activation_restricted' when activation fails."""
    mock_manage.side_effect = WindowOperationError("Windows did not allow foreground activation for window 1001.")

    data = ManageWindowInput(action="activate", hwnd=1001)
    result = manage_window_tool(data)

    assert result.success is False
    assert result.status == "activation_restricted"
    assert "foreground activation" in result.message


@patch.object(WindowManager, "minimize_window", return_value=True)
@patch.object(WindowManager, "get_window_info")
def test_sec006_minimize_maximize_restore_unaffected(mock_info, mock_min):
    """SEC-006: Minimize, maximize, and restore operations remain fully functional and unaffected."""
    info = WindowInfo(
        hwnd=1001, title="Test", app_id="notepad", display_name="Notepad", pid=1,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=True, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    mock_info.return_value = info

    wm = WindowManager()
    res = wm.manage_window("minimize", hwnd=1001)
    assert res.hwnd == 1001
    mock_min.assert_called_once_with(1001)


# ==============================================================================
# SEC-007 Regression Tests: HWND and app_name Co-Validation
# ==============================================================================

def test_sec007_hwnd_and_app_name_mismatch_rejected():
    """SEC-007: Passing an HWND belonging to Chrome while specifying app_name='notepad' must be rejected."""
    wm = WindowManager()
    chrome_info = WindowInfo(
        hwnd=2001, title="Google Chrome", app_id="chrome", display_name="Google Chrome", pid=50,
        process_name="chrome.exe", executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )

    with patch.object(wm, "get_window_info", return_value=chrome_info):
        with pytest.raises(WindowNotAllowedError, match="belongs to 'Google Chrome' \\(chrome\\), not the requested application 'Notepad'"):
            wm.manage_window("minimize", hwnd=2001, app_name="notepad")


def test_sec007_hwnd_and_app_name_matching_accepted():
    """SEC-007: Passing an HWND matching app_name succeeds."""
    wm = WindowManager()
    notepad_info = WindowInfo(
        hwnd=3001, title="Notes", app_id="notepad", display_name="Notepad", pid=60,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=True, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )

    with patch.object(wm, "get_window_info", return_value=notepad_info), \
         patch.object(wm, "minimize_window", return_value=True) as mock_min:
        res = wm.manage_window("minimize", hwnd=3001, app_name="notepad")
        assert res.app_id == "notepad"
        mock_min.assert_called_once_with(3001)


def test_sec007_invalid_app_name_with_hwnd_rejected():
    """SEC-007: Passing an invalid/unapproved application name with an HWND raises ApplicationNotAllowedError."""
    wm = WindowManager()
    with pytest.raises(ApplicationNotAllowedError):
        wm.manage_window("minimize", hwnd=1001, app_name="unapproved_cmd")


def test_sec007_unapproved_hwnd_with_app_name_rejected():
    """SEC-007: Passing an unapproved HWND with a valid app_name raises WindowNotAllowedError."""
    wm = WindowManager()
    with patch.object(wm, "get_window_info", side_effect=WindowNotAllowedError("Unapproved process")):
        with pytest.raises(WindowNotAllowedError):
            wm.manage_window("minimize", hwnd=9999, app_name="notepad")


@patch.object(WindowManager, "validate_window_ownership")
@patch.object(WindowManager, "is_window_responsive")
def test_activate_window_hung_raises_operation_error(mock_resp, mock_val):
    """activate_window refuses to operate on a hung window and raises WindowOperationError."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_resp.return_value = False

    wm = WindowManager()
    with pytest.raises(WindowOperationError, match="unresponsive/hung"):
        wm.activate_window(5002)


@patch.object(WindowManager, "validate_window_ownership")
@patch("backend.automation.window_manager.win32gui.IsIconic")
@patch("backend.automation.window_manager.win32gui.ShowWindow")
def test_minimize_window_success(mock_show, mock_is_iconic, mock_val):
    """minimize_window minimizes the window and verifies iconic state."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_is_iconic.return_value = True

    wm = WindowManager()
    result = wm.minimize_window(5003)

    assert result is True
    mock_show.assert_called_once()


@patch.object(WindowManager, "validate_window_ownership")
@patch("backend.automation.window_manager.ctypes.windll.user32.IsZoomed")
@patch("backend.automation.window_manager.win32gui.ShowWindow")
def test_maximize_window_success(mock_show, mock_is_zoomed, mock_val):
    """maximize_window maximizes the window and verifies zoomed state."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_is_zoomed.return_value = 1

    wm = WindowManager()
    result = wm.maximize_window(5004)

    assert result is True
    mock_show.assert_called_once()


@patch.object(WindowManager, "validate_window_ownership")
@patch("backend.automation.window_manager.ctypes.windll.user32.IsZoomed")
@patch("backend.automation.window_manager.win32gui.IsIconic")
@patch("backend.automation.window_manager.win32gui.ShowWindow")
def test_restore_window_success(mock_show, mock_is_iconic, mock_is_zoomed, mock_val):
    """restore_window restores the window and verifies neither iconic nor zoomed."""
    mock_val.return_value = ("notepad", "Notepad", 100, "notepad.exe", r"C:\Windows\System32\notepad.exe")
    mock_is_iconic.return_value = False
    mock_is_zoomed.return_value = 0

    wm = WindowManager()
    result = wm.restore_window(5005)

    assert result is True
    mock_show.assert_called_once()


def test_operation_on_unapproved_window_rejected_without_modifying_state():
    """Attempting any operation on an unapproved window fails validation immediately."""
    wm = WindowManager()
    with patch.object(wm, "validate_window_ownership", side_effect=WindowNotAllowedError("Not allowed")):
        with pytest.raises(WindowNotAllowedError):
            wm.minimize_window(9999)


# ==============================================================================
# 7. Multi-Window Handling and Ambiguity Tests
# ==============================================================================

@patch.object(WindowManager, "find_windows")
def test_manage_window_ambiguity_raises_window_ambiguity_error(mock_find):
    """manage_window raises WindowAmbiguityError if multiple windows match and no hwnd is provided."""
    info1 = WindowInfo(
        hwnd=6001, title="Doc 1", app_id="notepad", display_name="Notepad", pid=1,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    info2 = WindowInfo(
        hwnd=6002, title="Doc 2", app_id="notepad", display_name="Notepad", pid=2,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    mock_find.return_value = [info1, info2]

    wm = WindowManager()
    with pytest.raises(WindowAmbiguityError) as exc_info:
        wm.manage_window(action="minimize", app_name="notepad")

    assert len(exc_info.value.matching_windows) == 2
    assert exc_info.value.matching_windows[0].hwnd == 6001
    assert exc_info.value.matching_windows[1].hwnd == 6002


@patch.object(WindowManager, "get_window_info")
@patch.object(WindowManager, "minimize_window")
def test_manage_window_explicit_hwnd_succeeds_without_ambiguity(mock_min, mock_info):
    """manage_window targets explicit hwnd directly even when multiple windows might exist."""
    info = WindowInfo(
        hwnd=6002, title="Doc 2", app_id="notepad", display_name="Notepad", pid=2,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=True, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    mock_info.return_value = info

    wm = WindowManager()
    result = wm.manage_window(action="minimize", hwnd=6002)

    mock_min.assert_called_once_with(6002)
    assert result.hwnd == 6002


# ==============================================================================
# 8. Tool Interface Schema and Execution Tests
# ==============================================================================

def test_find_windows_input_sanitization():
    """FindWindowsInput sanitizes and rejects path separators and injection characters."""
    # Valid
    inp = FindWindowsInput(application_name="notepad", title_pattern="notes")
    assert inp.application_name == "notepad"
    assert inp.title_pattern == "notes"

    # Path separator rejected
    with pytest.raises(ValueError, match="path separators"):
        FindWindowsInput(application_name="notepad/calc")

    # Command injection rejected
    with pytest.raises(ValueError, match="invalid characters or shell operators"):
        FindWindowsInput(application_name="notepad; calc")


def test_manage_window_input_validation():
    """ManageWindowInput enforces valid actions and requires either hwnd or app_name."""
    # Invalid action
    with pytest.raises(ValueError, match="Allowed actions"):
        ManageWindowInput(action="destroy", hwnd=123)

    # Missing both hwnd and app_name
    with pytest.raises(ValueError, match="Either 'hwnd' or 'application_name' must be provided"):
        ManageWindowInput(action="minimize")

    # Non-positive HWND
    with pytest.raises(ValueError, match="positive integer"):
        ManageWindowInput(action="minimize", hwnd=-5)


@patch.object(WindowManager, "find_windows")
def test_find_windows_tool_success(mock_find):
    """find_windows_tool returns structured FindWindowsResult."""
    info = WindowInfo(
        hwnd=7001, title="Test App", app_id="notepad", display_name="Notepad", pid=10,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,100,100)
    )
    mock_find.return_value = [info]

    data = FindWindowsInput(application_name="notepad")
    result = find_windows_tool(data)

    assert result.success is True
    assert result.count == 1
    assert result.windows[0]["hwnd"] == 7001
    assert result.status == "success"


def test_find_windows_tool_unapproved_app():
    """find_windows_tool handles ApplicationNotAllowedError safely."""
    data = FindWindowsInput(application_name="powershell")
    result = find_windows_tool(data)

    assert result.success is False
    assert result.status == "not_allowed"
    assert result.count == 0


@patch.object(WindowManager, "manage_window")
def test_manage_window_tool_ambiguity_handling(mock_manage):
    """manage_window_tool returns ambiguous status and matching windows list."""
    info1 = WindowInfo(
        hwnd=8001, title="Doc 1", app_id="notepad", display_name="Notepad", pid=1,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    info2 = WindowInfo(
        hwnd=8002, title="Doc 2", app_id="notepad", display_name="Notepad", pid=2,
        process_name="notepad.exe", executable_path=r"C:\Windows\System32\notepad.exe",
        is_visible=True, is_minimized=False, is_maximized=False, is_responsive=True, rect=(0,0,10,10)
    )
    mock_manage.side_effect = WindowAmbiguityError("Multiple windows found", matching_windows=[info1, info2])

    data = ManageWindowInput(action="minimize", application_name="notepad")
    result = manage_window_tool(data)

    assert result.success is False
    assert result.status == "ambiguous"
    assert len(result.matching_windows) == 2
    assert result.matching_windows[0]["hwnd"] == 8001


# ==============================================================================
# 9. Live Notepad Verification Test (Opt-In with @pytest.mark.live)
# ==============================================================================

@pytest.mark.live
def test_live_notepad_window_management():
    """Live verification test: launch Notepad, find its window, minimize, restore, and terminate test process."""
    import subprocess
    import psutil
    from backend.automation.application_launcher import ApplicationLauncher

    launcher = ApplicationLauncher()
    launch_res = launcher.launch("notepad")
    time.sleep(2.0)

    wm = WindowManager()
    launched_alias_pid = launch_res["pid"]
    target_pid = None

    try:
        # 1. Discover Notepad windows
        windows = wm.find_windows(app_name="notepad", visible_only=True)
        assert len(windows) >= 1, "Expected to find at least one visible Notepad window"

        target_win = windows[0]
        assert target_win.app_id == "notepad"
        assert target_win.process_name.lower() == "notepad.exe"
        target_pid = target_win.pid

        # 2. Minimize window
        min_result = wm.minimize_window(target_win.hwnd)
        assert min_result is True, "Expected window to be successfully minimized"
        info_min = wm.get_window_info(target_win.hwnd)
        assert info_min.is_minimized is True

        # 3. Restore window
        restore_result = wm.restore_window(target_win.hwnd)
        assert restore_result is True, "Expected window to be successfully restored"
        info_restored = wm.get_window_info(target_win.hwnd)
        assert info_restored.is_minimized is False

    finally:
        # Strictly clean up ONLY our launched test processes
        pids_to_clean = {p for p in (launched_alias_pid, target_pid) if p is not None}
        for pid in pids_to_clean:
            try:
                p = psutil.Process(pid)
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass
