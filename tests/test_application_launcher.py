"""
VoxPilot - Application Launcher and Tool Subsystem Tests
Tests for Phase 3 Step 1: Safe Windows Application Launching.
Covers allowlist resolution, executable discovery, registry fallback, safe process spawning,
error handling, tool wrapper validation, and an opt-in live Notepad test.
"""

import os
import sys
import psutil
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    import winreg
except ImportError:
    winreg = None

from backend.automation.application_launcher import (
    ApplicationConfig,
    ApplicationRegistry,
    ApplicationLauncher,
    ApplicationLauncherError,
    ApplicationNotAllowedError,
    ApplicationNotFoundError,
    DEFAULT_APPROVED_APPLICATIONS,
    is_path_in_approved_directories,
    validate_registry_value,
)
from backend.tools.applications import (
    LaunchApplicationInput,
    LaunchApplicationResult,
    launch_application_tool,
)


# =====================================================================
# 1-4. Allowlist Lookup, Alias Resolution, Case & Whitespace Normalization
# =====================================================================

def test_canonical_application_lookup():
    """Verify registry resolves exact canonical application IDs."""
    registry = ApplicationRegistry()

    assert registry.resolve("notepad").app_id == "notepad"
    assert registry.resolve("calculator").app_id == "calculator"
    assert registry.resolve("chrome").app_id == "chrome"
    assert registry.resolve("vscode").app_id == "vscode"


def test_alias_resolution():
    """Verify registry maps aliases to canonical application IDs."""
    registry = ApplicationRegistry()

    assert registry.resolve("text editor").app_id == "notepad"
    assert registry.resolve("notes").app_id == "notepad"
    assert registry.resolve("calc").app_id == "calculator"
    assert registry.resolve("google chrome").app_id == "chrome"
    assert registry.resolve("web browser").app_id == "chrome"
    assert registry.resolve("vs code").app_id == "vscode"
    assert registry.resolve("code").app_id == "vscode"
    assert registry.resolve("visual studio code").app_id == "vscode"


def test_case_insensitive_matching():
    """Verify query resolution is case-insensitive."""
    registry = ApplicationRegistry()

    assert registry.resolve("NOTEPAD").app_id == "notepad"
    assert registry.resolve("Google Chrome").app_id == "chrome"
    assert registry.resolve("CALC").app_id == "calculator"
    assert registry.resolve("VsCode").app_id == "vscode"
    assert registry.resolve("vIsUaL StUdIo CoDe").app_id == "vscode"


def test_whitespace_and_extension_normalization():
    """Verify whitespace and .exe suffixes are stripped during resolution."""
    registry = ApplicationRegistry()

    assert registry.resolve("  notepad  ").app_id == "notepad"
    assert registry.resolve("notepad.exe").app_id == "notepad"
    assert registry.resolve("  calc.exe  ").app_id == "calculator"
    assert registry.resolve("chrome.exe").app_id == "chrome"
    assert registry.resolve("code.exe").app_id == "vscode"


# =====================================================================
# 5-7. Security: Unknown App, Arbitrary Path, and Shell Injection Rejection
# =====================================================================

def test_unknown_application_rejection():
    """Verify unapproved applications are rejected with ApplicationNotAllowedError."""
    registry = ApplicationRegistry()

    unapproved = ["powershell", "cmd", "regedit", "taskmgr", "random_malware", "bash"]
    for app in unapproved:
        with pytest.raises(ApplicationNotAllowedError) as exc_info:
            registry.resolve(app)
        assert "not on the approved application allowlist" in str(exc_info.value)


def test_empty_and_invalid_type_query_rejection():
    """Verify empty or non-string inputs are rejected."""
    registry = ApplicationRegistry()

    with pytest.raises(ApplicationNotAllowedError):
        registry.resolve("")

    with pytest.raises(ApplicationNotAllowedError):
        registry.resolve("   ")

    with pytest.raises(ApplicationNotAllowedError):
        registry.resolve(None)  # type: ignore


def test_arbitrary_path_and_traversal_rejection():
    """Verify arbitrary file paths and directory traversals are rejected by allowlist."""
    registry = ApplicationRegistry()

    paths_to_reject = [
        r"C:\Windows\System32\cmd.exe",
        r"C:\Windows\System32\notepad.exe",
        r"..\..\malicious.exe",
        r"/usr/bin/python",
        r"\\remote_share\payload.exe",
    ]
    for path in paths_to_reject:
        with pytest.raises(ApplicationNotAllowedError):
            registry.resolve(path)


def test_shell_injection_patterns_rejected():
    """Verify shell chaining operators and command injection attempts are rejected."""
    registry = ApplicationRegistry()

    injections = [
        "notepad.exe & dir",
        "calc | calc",
        "notepad; powershell",
        "chrome && calc",
        "notepad `whoami`",
        "calc > output.txt",
    ]
    for injection in injections:
        with pytest.raises(ApplicationNotAllowedError):
            registry.resolve(injection)


# =====================================================================
# 8-10. Executable Path Resolution, Registry Fallback, Missing Executable
# =====================================================================

def test_find_executable_from_candidates(tmp_path):
    """Verify find_executable discovers an existing candidate path."""
    fake_exe = tmp_path / "mock_app.exe"
    fake_exe.write_text("binary content")

    config = ApplicationConfig(
        app_id="mock_app",
        display_name="Mock App",
        aliases=["mock"],
        executable_candidates=[fake_exe],
        approved_directories=[tmp_path],
    )
    launcher = ApplicationLauncher()
    resolved = launcher.find_executable(config)

    assert resolved == fake_exe.resolve()


def test_find_executable_registry_fallback(tmp_path):
    """Verify find_executable falls back to Windows Registry App Paths when candidates miss."""
    fake_reg_exe = tmp_path / "from_registry.exe"
    fake_reg_exe.write_text("binary content")

    config = ApplicationConfig(
        app_id="test_reg",
        display_name="Test Reg App",
        aliases=["testreg"],
        executable_candidates=[tmp_path / "non_existent.exe"],
        registry_app_path="testreg.exe",
        approved_directories=[tmp_path],
    )
    launcher = ApplicationLauncher()

    # Mock _query_registry_app_path to return fake_reg_exe
    with patch.object(launcher, "_query_registry_app_path", return_value=fake_reg_exe.resolve()):
        resolved = launcher.find_executable(config)
        assert resolved == fake_reg_exe.resolve()


def test_missing_executable_raises_not_found(tmp_path):
    """Verify missing executable on disk raises ApplicationNotFoundError without falling back to PATH."""
    config = ApplicationConfig(
        app_id="ghost_app",
        display_name="Ghost App",
        aliases=["ghost"],
        executable_candidates=[tmp_path / "ghost.exe"],
        registry_app_path="ghost.exe",
        approved_directories=[tmp_path],
    )
    launcher = ApplicationLauncher()

    with patch.object(launcher, "_query_registry_app_path", return_value=None):
        with pytest.raises(ApplicationNotFoundError) as exc_info:
            launcher.find_executable(config)
        assert "could not be located" in str(exc_info.value)


# =====================================================================
# 11-17. Safe Process Execution, shell=False, Error Handling, and Result
# =====================================================================

def test_launch_approved_application_mock_popen(tmp_path):
    """Verify launch spawns process with exact argument list, shell=False, and returns result."""
    fake_exe = tmp_path / "chrome.exe"
    fake_exe.write_text("binary")

    config = ApplicationConfig(
        app_id="chrome",
        display_name="Google Chrome",
        aliases=["chrome"],
        executable_candidates=[fake_exe],
        approved_directories=[tmp_path],
    )
    registry = ApplicationRegistry(applications={"chrome": config})
    launcher = ApplicationLauncher(registry=registry)

    mock_process = MagicMock()
    mock_process.pid = 4321

    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        result = launcher.launch("chrome")

        assert result["success"] is True
        assert result["app_id"] == "chrome"
        assert result["display_name"] == "Google Chrome"
        assert result["pid"] == 4321
        assert result["status"] == "launched"
        assert "timestamp" in result

        # Critical Security Assertions
        mock_popen.assert_called_once()
        call_args, call_kwargs = mock_popen.call_args

        # 1. First argument must be a list containing ONLY the validated executable path
        assert call_args[0] == [str(fake_exe.resolve())]

        # 2. shell=False MUST be strictly enforced
        assert call_kwargs.get("shell") is False

        # 3. No arbitrary arguments forwarded
        assert len(call_args[0]) == 1


def test_launch_process_spawn_os_error_handling(tmp_path):
    """Verify OSError during process creation raises ApplicationLauncherError."""
    fake_exe = tmp_path / "notepad.exe"
    fake_exe.write_text("binary")

    config = ApplicationConfig(
        app_id="notepad",
        display_name="Notepad",
        aliases=["notepad"],
        executable_candidates=[fake_exe],
        approved_directories=[tmp_path],
    )
    registry = ApplicationRegistry(applications={"notepad": config})
    launcher = ApplicationLauncher(registry=registry)

    with patch("subprocess.Popen", side_effect=OSError("Resource unavailable")):
        with pytest.raises(ApplicationLauncherError) as exc_info:
            launcher.launch("notepad")
        assert "Failed to launch 'Notepad'" in str(exc_info.value)


def test_launch_permission_error_handling(tmp_path):
    """Verify PermissionError during process creation raises ApplicationLauncherError."""
    fake_exe = tmp_path / "notepad.exe"
    fake_exe.write_text("binary")

    config = ApplicationConfig(
        app_id="notepad",
        display_name="Notepad",
        aliases=["notepad"],
        executable_candidates=[fake_exe],
        approved_directories=[tmp_path],
    )
    registry = ApplicationRegistry(applications={"notepad": config})
    launcher = ApplicationLauncher(registry=registry)

    with patch("subprocess.Popen", side_effect=PermissionError("Access is denied")):
        with pytest.raises(ApplicationLauncherError) as exc_info:
            launcher.launch("notepad")
        assert "Failed to launch 'Notepad'" in str(exc_info.value)


# =====================================================================
# 18. Tool Wrapper Validation (backend/tools/applications.py)
# =====================================================================

def test_tool_input_pydantic_validation_success():
    """Verify valid application names pass Pydantic schema validation."""
    valid_inputs = ["Notepad", "Calculator", "Google Chrome", "Visual Studio Code", "calc"]
    for val in valid_inputs:
        model = LaunchApplicationInput(application_name=val)
        assert model.application_name == val.strip()


def test_tool_input_pydantic_validation_rejection():
    """Verify invalid names (empty, path separators, shell operators, flags) are rejected."""
    invalid_cases = [
        "",
        "   ",
        "a" * 105,  # Exceeds max length
        "C:\\Windows\\cmd.exe",
        "/bin/sh",
        "notepad; calc",
        "notepad & dir",
        "-option",
        "/flag",
    ]
    for case in invalid_cases:
        with pytest.raises(ValueError):
            LaunchApplicationInput(application_name=case)


def test_launch_application_tool_success_outcome():
    """Verify launch_application_tool returns structured LaunchApplicationResult on success."""
    mock_launcher = MagicMock()
    mock_launcher.launch.return_value = {
        "success": True,
        "app_id": "notepad",
        "display_name": "Notepad",
        "pid": 9999,
        "status": "launched",
        "message": "Successfully launched Notepad (PID: 9999).",
        "timestamp": "2026-11-10T00:00:00Z",
    }

    tool_input = LaunchApplicationInput(application_name="notepad")
    result = launch_application_tool(tool_input, launcher=mock_launcher)

    assert isinstance(result, LaunchApplicationResult)
    assert result.success is True
    assert result.app_id == "notepad"
    assert result.pid == 9999
    assert result.status == "launched"


def test_launch_application_tool_not_allowed_outcome():
    """Verify launch_application_tool handles ApplicationNotAllowedError cleanly."""
    mock_launcher = MagicMock()
    mock_launcher.launch.side_effect = ApplicationNotAllowedError("Application 'malware' is not approved.")

    tool_input = LaunchApplicationInput(application_name="malware")
    result = launch_application_tool(tool_input, launcher=mock_launcher)

    assert result.success is False
    assert result.status == "rejected"
    assert result.pid is None
    assert "not approved" in result.message


def test_launch_application_tool_not_found_outcome():
    """Verify launch_application_tool handles ApplicationNotFoundError cleanly."""
    mock_launcher = MagicMock()
    mock_launcher.launch.side_effect = ApplicationNotFoundError("Executable for 'Chrome' could not be located.")

    tool_input = LaunchApplicationInput(application_name="chrome")
    result = launch_application_tool(tool_input, launcher=mock_launcher)

    assert result.success is False
    assert result.status == "not_found"
    assert result.pid is None
    assert "could not be located" in result.message


# =====================================================================
# 19. Application Running State Checks (is_running)
# =====================================================================

def test_is_running_matches_mock_process():
    """Verify is_running matches running process names."""
    launcher = ApplicationLauncher()

    # Create mock psutil process
    mock_proc = MagicMock()
    mock_proc.info = {"name": "notepad.exe", "exe": r"C:\Windows\System32\notepad.exe"}

    with patch("psutil.process_iter", return_value=[mock_proc]):
        assert launcher.is_running("notepad") is True
        assert launcher.is_running("chrome") is False


# =====================================================================
# 20. Security Regression Tests (SEC-001, SEC-002, SEC-003)
# =====================================================================

def test_sec001_path_lookup_is_never_invoked(tmp_path):
    """SEC-001: Verify find_executable never invokes ambient PATH lookup."""
    config = ApplicationConfig(
        app_id="ghost_app",
        display_name="Ghost App",
        aliases=["ghost"],
        executable_candidates=[tmp_path / "missing.exe"],
        registry_app_path="ghost.exe",
        approved_directories=[tmp_path],
    )
    launcher = ApplicationLauncher()

    with patch.object(launcher, "_query_registry_app_path", return_value=None):
        with pytest.raises(ApplicationNotFoundError):
            launcher.find_executable(config)


def test_sec001_executable_planted_in_path_directory_never_selected(tmp_path, monkeypatch):
    """SEC-001: Verify a Trojan binary matching expected name in PATH is NEVER selected."""
    # Create fake untrusted directory with a planted calc.exe
    untrusted_dir = tmp_path / "untrusted_path_bin"
    untrusted_dir.mkdir()
    planted_calc = untrusted_dir / "calc.exe"
    planted_calc.write_text("malicious payload")

    # Put untrusted directory at the very front of system PATH
    monkeypatch.setenv("PATH", f"{untrusted_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    # Configure an app pointing to non-existent candidates and missing registry
    config = ApplicationConfig(
        app_id="calculator",
        display_name="Calculator",
        aliases=["calc"],
        executable_candidates=[tmp_path / "nonexistent" / "calc.exe"],
        registry_app_path="calc.exe",
        approved_directories=[tmp_path / "approved"],
    )
    launcher = ApplicationLauncher()

    with patch.object(launcher, "_query_registry_app_path", return_value=None):
        with pytest.raises(ApplicationNotFoundError):
            launcher.find_executable(config)


def test_sec002_registry_hklm_checked_before_hkcu():
    """SEC-002: Verify Windows Registry App Paths checks HKLM before HKCU."""
    if winreg is None:
        pytest.skip("winreg not available")

    launcher = ApplicationLauncher()
    roots_queried = []

    def mock_open_key(root, sub_key, reserved, access):
        roots_queried.append(root)
        raise FileNotFoundError("Mock key missing")

    with patch("winreg.OpenKey", side_effect=mock_open_key):
        launcher._query_registry_app_path("chrome.exe", approved_dirs=[])

    assert roots_queried == [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]


def test_sec002_registry_hkcu_undergoes_same_strict_validation_as_hklm(tmp_path):
    """SEC-002: Verify HKCU results undergo identical validation and cannot bypass rules."""
    if winreg is None:
        pytest.skip("winreg not available")

    launcher = ApplicationLauncher()

    # Create unapproved executable
    untrusted_file = tmp_path / "untrusted" / "chrome.exe"
    untrusted_file.parent.mkdir()
    untrusted_file.write_text("fake binary")

    approved_dir = tmp_path / "approved_dir"
    approved_dir.mkdir()

    # HKLM raises FileNotFoundError, HKCU returns unapproved path
    def mock_open_key(root, sub_key, reserved, access):
        if root == winreg.HKEY_LOCAL_MACHINE:
            raise FileNotFoundError()
        return MagicMock()

    with patch("winreg.OpenKey", side_effect=mock_open_key), \
         patch("winreg.QueryValueEx", return_value=(str(untrusted_file), 1)):

        # Should be rejected because untrusted_file is outside approved_dir
        result = launcher._query_registry_app_path("chrome.exe", approved_dirs=[approved_dir])
        assert result is None


def test_sec003_registry_filename_mismatch_rejected(tmp_path):
    """SEC-003: Reject registry path if the filename does not match expected_filename."""
    app_dir = tmp_path / "approved"
    app_dir.mkdir()
    evil_file = app_dir / "evil.exe"
    evil_file.write_text("evil")

    # Looking for chrome.exe, but registry points to evil.exe
    result = validate_registry_value(
        raw_value=str(evil_file),
        expected_filename="chrome.exe",
        approved_dirs=[app_dir],
    )
    assert result is None


def test_sec003_registry_values_with_arguments_rejected(tmp_path):
    """SEC-003: Reject registry values that append flags or arguments."""
    app_dir = tmp_path / "approved"
    app_dir.mkdir()
    chrome_file = app_dir / "chrome.exe"
    chrome_file.write_text("binary")

    # 1. Quoted with trailing arguments
    quoted_with_args = f'"{chrome_file}" --disable-web-security'
    assert validate_registry_value(quoted_with_args, "chrome.exe", [app_dir]) is None

    # 2. Unquoted with flag argument
    unquoted_with_args = f"{chrome_file} /incognito"
    assert validate_registry_value(unquoted_with_args, "chrome.exe", [app_dir]) is None

    # 3. Shell chaining operator
    chained_val = f"{chrome_file} & calc.exe"
    assert validate_registry_value(chained_val, "chrome.exe", [app_dir]) is None


def test_sec003_registry_values_pointing_outside_approved_directories_rejected(tmp_path):
    """SEC-003: Reject registry executable outside configured approved directories."""
    unapproved_dir = tmp_path / "unapproved"
    unapproved_dir.mkdir()
    chrome_file = unapproved_dir / "chrome.exe"
    chrome_file.write_text("binary")

    approved_dir = tmp_path / "approved"
    approved_dir.mkdir()

    # Points to chrome_file in unapproved_dir
    result = validate_registry_value(
        raw_value=str(chrome_file),
        expected_filename="chrome.exe",
        approved_dirs=[approved_dir],
    )
    assert result is None


def test_sec003_registry_unresolved_env_vars_rejected(tmp_path):
    """SEC-003: Reject registry values with unresolved environment variables."""
    approved_dir = tmp_path / "approved"
    approved_dir.mkdir()

    unresolved_val = r"%UNRESOLVED_APP_DIR%\chrome.exe"
    result = validate_registry_value(
        raw_value=unresolved_val,
        expected_filename="chrome.exe",
        approved_dirs=[approved_dir],
    )
    assert result is None


def test_sec003_registry_missing_or_non_string_values_handled_safely(tmp_path):
    """SEC-003: Safely reject empty, None, and non-string registry values."""
    approved_dir = tmp_path / "approved"
    approved_dir.mkdir()

    assert validate_registry_value("", "chrome.exe", [approved_dir]) is None
    assert validate_registry_value("   ", "chrome.exe", [approved_dir]) is None
    assert validate_registry_value(None, "chrome.exe", [approved_dir]) is None
    assert validate_registry_value(12345, "chrome.exe", [approved_dir]) is None
    assert validate_registry_value(["chrome.exe"], "chrome.exe", [approved_dir]) is None


def test_sec003_invalid_registry_results_cannot_bypass_allowlist():
    """SEC-003: Verify unapproved applications are rejected before registry lookup."""
    launcher = ApplicationLauncher()

    with patch.object(launcher, "_query_registry_app_path") as mock_reg:
        with pytest.raises(ApplicationNotAllowedError):
            launcher.launch("unapproved_malware")
        mock_reg.assert_not_called()


def test_sec003_valid_registry_executable_in_approved_location_resolves(tmp_path):
    """SEC-003: Verify legitimate executable in approved location resolves successfully."""
    approved_dir = tmp_path / "approved"
    approved_dir.mkdir()
    valid_file = approved_dir / "chrome.exe"
    valid_file.write_text("binary")

    raw_val = f'"{valid_file}"'
    resolved = validate_registry_value(
        raw_value=raw_val,
        expected_filename="chrome.exe",
        approved_dirs=[approved_dir],
    )
    assert resolved == valid_file.resolve()


def test_sec003_symlink_redirection_outside_approved_directory_rejected(tmp_path):
    """SEC-003: Verify symlink resolving outside approved directory is detected and rejected."""
    approved_dir = tmp_path / "approved"
    approved_dir.mkdir()
    link_file = approved_dir / "chrome.exe"
    link_file.write_text("placeholder")

    untrusted_target = tmp_path / "untrusted_outside" / "chrome.exe"
    untrusted_target.parent.mkdir()
    untrusted_target.write_text("evil target")

    orig_resolve = Path.resolve

    def mock_resolve(self, *args, **kwargs):
        # Only redirect resolution of link_file to untrusted_target
        if str(self) == str(link_file):
            return untrusted_target
        return orig_resolve(self, *args, **kwargs)

    with patch.object(Path, "resolve", side_effect=mock_resolve, autospec=True):
        result = validate_registry_value(
            raw_value=str(link_file),
            expected_filename="chrome.exe",
            approved_dirs=[approved_dir],
        )
        assert result is None


# =====================================================================
# 21. Opt-in Live Windows Test (Runs only when explicitly opted in)
# =====================================================================

@pytest.mark.live
def test_live_launch_and_cleanup_notepad():
    """Opt-in live test: Launches Notepad on host Windows 11, verifies PID, and cleanly terminates it.

    Safeguards:
    - Records pre-existing Notepad PIDs to ensure they are never touched.
    - Launches Notepad via real ApplicationLauncher.
    - Verifies the spawned PID is alive and running 'notepad.exe'.
    - Unconditionally terminates ONLY the test-spawned PID in finally block.
    """
    if sys.platform != "win32":
        pytest.skip("Live application launch test only supported on Windows.")

    # Record any pre-existing Notepad PIDs to preserve user's open documents
    initial_notepad_pids = set()
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if (proc.info.get("name") or "").lower() == "notepad.exe":
                initial_notepad_pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    launcher = ApplicationLauncher()
    spawned_pid = None

    try:
        # Launch Notepad through the production application launcher
        result = launcher.launch("notepad")

        assert result["success"] is True
        assert result["app_id"] == "notepad"
        assert result["status"] == "launched"
        spawned_pid = result["pid"]
        assert spawned_pid is not None
        assert spawned_pid not in initial_notepad_pids, "Spawned PID collided with pre-existing process"

        # Verify process is alive and its executable is notepad.exe
        proc = psutil.Process(spawned_pid)
        assert proc.is_running(), f"Spawned process {spawned_pid} was not running"
        assert "notepad" in proc.name().lower()

    finally:
        # Cleanup: Terminate ONLY the spawned process
        if spawned_pid is not None:
            try:
                proc = psutil.Process(spawned_pid)
                if proc.is_running():
                    proc.terminate()
                    proc.wait(timeout=3.0)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                try:
                    proc.kill()
                except Exception:
                    pass

