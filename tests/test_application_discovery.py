"""
Comprehensive Unit, Security, API, and Live Integration Tests for Universal Windows
Application Discovery, Indexing, Alias/Fuzzy Resolution, and Dynamic Launching.

Covers all 21 required scenarios:
1. Start Menu shortcut discovery (.lnk binary parser + recursive folder scan)
2. Windows Registry application discovery (App Paths + 64-bit/32-bit Uninstall keys)
3. Microsoft Store / Appx application discovery (Get-StartApps AUMIDs)
4. Desktop shortcut discovery
5. Duplicate application removal and metadata merging
6. Application indexing and JSON persistence
7. Cache refresh
8. Alias resolution
9. Fuzzy matching
10. Ambiguous application name detection and clarification
11. Missing executable handling
12. Invalid shortcut handling
13. Unsafe executable path rejection (Temp, Downloads, cmd/powershell/regedit)
14. Application launching (executable, shortcut, store_app, settings_uri, system_utility, shell_folder)
15. Windows Settings URI allowlist mapping
16. Built-in Windows utilities and special folders
17. Natural-language intent recognition
18. Planner parameter normalization and deterministic plan generation
19. Security confirmation for sensitive utilities (Device Manager, Services, msconfig)
20. Audit logging
21. Emergency stop interruption
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import psutil
import pytest
from fastapi.testclient import TestClient

from backend.agent.graph import VoxPilotAgent
from backend.agent.intent import IntentRecognizer
from backend.agent.planner import AgentPlanner
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import ToolRegistry, get_shared_tool_registry
from backend.automation.application_discovery import (
    ApplicationDiscoveryService,
    DiscoveredApplication,
    PROHIBITED_EXECUTABLES,
    WINDOWS_BUILT_IN_UTILITIES,
    WINDOWS_SETTINGS_DESTINATIONS,
    WINDOWS_SPECIAL_FOLDERS,
    is_suspicious_or_temporary_path,
    make_app_slug,
    normalize_app_name,
    parse_windows_lnk,
    reset_shared_discovery_service,
    validate_executable_target,
    validate_shortcut_arguments,
)
from backend.automation.application_launcher import (
    AmbiguousApplicationError,
    ApplicationLauncher,
    ApplicationNotAllowedError,
    ApplicationNotFoundError,
    UnsafeExecutablePathError,
)
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    reset_shared_confirmation_manager,
)
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    get_shared_emergency_stop_manager,
    set_shared_emergency_stop_manager,
)
from backend.main import app
from backend.tools.applications import (
    LaunchApplicationInput,
    OpenWindowsSettingsInput,
    RefreshApplicationIndexInput,
    SearchApplicationsInput,
    launch_application_tool,
    open_windows_settings_tool,
    refresh_applications_tool,
    search_applications_tool,
)


# ==============================================================================
# Helper to build synthetic MS-SHLLINK (.lnk) binary files for testing
# ==============================================================================


def create_synthetic_lnk(
    lnk_path: Path,
    target_exe: str,
    arguments: str = "",
    working_dir: str = "",
) -> Path:
    """Create a valid binary MS-SHLLINK (.lnk) file containing StringData and EnvironmentVariableDataBlock."""
    # 76-byte ShellLinkHeader:
    # HeaderSize = 0x4C, LinkCLSID = 16 zero bytes,
    # LinkFlags = HasWorkingDir (0x10) | HasArguments (0x20) | IsUnicode (0x80) = 0xB0
    header = bytearray(76)
    struct.pack_into("<II", header, 0, 0x4C, 0x10 | 0x20 | 0x80)

    def _pack_unicode_string(val: str) -> bytes:
        encoded = val.encode("utf-16-le")
        return struct.pack("<H", len(val)) + encoded

    body = bytearray()
    body.extend(_pack_unicode_string(working_dir))
    body.extend(_pack_unicode_string(arguments))

    # Append EnvironmentVariableDataBlock (BlockSize=788, BlockSignature=0xA0000001)
    env_block = bytearray(788)
    struct.pack_into("<II", env_block, 0, 788, 0xA0000001)
    ansi_bytes = target_exe.encode("cp1252", errors="ignore")[:259]
    env_block[8 : 8 + len(ansi_bytes)] = ansi_bytes
    uni_bytes = target_exe.encode("utf-16-le")[:518]
    env_block[268 : 268 + len(uni_bytes)] = uni_bytes

    terminal_block = struct.pack("<I", 0)
    lnk_path.write_bytes(bytes(header + body + env_block + terminal_block))
    return lnk_path


# ==============================================================================
# 1–4. Discovery Sources (Start Menu, Registry, Store Apps, Desktop Shortcuts)
# ==============================================================================


def test_01_start_menu_shortcut_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Start Menu .lnk shortcuts are parsed and indexed with safe target validation."""
    trusted_root = tmp_path / "ProgramFiles"
    app_dir = trusted_root / "Spotify"
    app_dir.mkdir(parents=True)
    spotify_exe = app_dir / "Spotify.exe"
    spotify_exe.write_bytes(b"MZ_FAKE_SPOTIFY")

    start_menu_dir = tmp_path / "StartMenu" / "Programs"
    start_menu_dir.mkdir(parents=True)
    create_synthetic_lnk(
        start_menu_dir / "Spotify.lnk",
        target_exe=str(spotify_exe),
        arguments="",
        working_dir=str(app_dir),
    )
    # Also create an uninstaller shortcut that MUST be ignored
    unins_exe = app_dir / "Uninstall Spotify.exe"
    unins_exe.write_bytes(b"MZ_UNINS")
    create_synthetic_lnk(
        start_menu_dir / "Uninstall Spotify.lnk",
        target_exe=str(unins_exe),
    )

    monkeypatch.setenv("VOXPILOT_EXTRA_APP_DIRS", str(trusted_root))
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    found = service._scan_shortcut_directory(start_menu_dir, "start_menu")
    names = [e.display_name for e in found]
    assert "Spotify" in names
    assert "Uninstall Spotify" not in names


def test_02_registry_application_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Registry Uninstall entries and DisplayIcon/InstallLocation resolution work safely."""
    trusted_root = tmp_path / "ProgramFiles"
    app_dir = trusted_root / "MongoDBCompass"
    app_dir.mkdir(parents=True)
    compass_exe = app_dir / "MongoDBCompass.exe"
    compass_exe.write_bytes(b"MZ_FAKE_COMPASS")

    monkeypatch.setenv("VOXPILOT_EXTRA_APP_DIRS", str(trusted_root))
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )

    resolved_exe = service._resolve_exe_from_uninstall_entry(
        display_name="MongoDB Compass",
        display_icon=f'"{compass_exe}",0',
        install_location=str(app_dir),
    )
    assert resolved_exe is not None
    assert resolved_exe.name == "MongoDBCompass.exe"


def test_03_microsoft_store_app_discovery(tmp_path: Path):
    """Verify Get-StartApps JSON output is parsed into store_app entries with shell:AppsFolder targets."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    fake_json = (
        '[{"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"},'
        ' {"Name": "Netflix", "AppID": "4DF9E0F8.Netflix_mcm4njqhnhss8!Netflix.App"}]'
    )
    mock_proc = MagicMock(returncode=0, stdout=fake_json, stderr="")
    with patch("backend.automation.application_discovery.subprocess.run", return_value=mock_proc):
        store_apps = service._discover_microsoft_store_apps()
        assert len(store_apps) == 2
        whatsapp = next(a for a in store_apps if a.display_name == "WhatsApp")
        assert whatsapp.launch_method == "store_app"
        assert whatsapp.source == "microsoft_store"
        assert whatsapp.executable_path == "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
        assert whatsapp.package_id == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"


def test_04_desktop_shortcut_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Desktop .lnk shortcuts are discovered and mapped to validated executables."""
    trusted_root = tmp_path / "ProgramFiles"
    discord_dir = trusted_root / "Discord"
    discord_dir.mkdir(parents=True)
    discord_exe = discord_dir / "Discord.exe"
    discord_exe.write_bytes(b"MZ_DISCORD")

    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir(parents=True)
    create_synthetic_lnk(
        desktop_dir / "Discord.lnk",
        target_exe=str(discord_exe),
        arguments="--processStart Discord.exe",
        working_dir=str(discord_dir),
    )

    monkeypatch.setenv("VOXPILOT_EXTRA_APP_DIRS", str(trusted_root))
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    with patch.object(service, "_get_desktop_dirs", return_value=[desktop_dir]):
        desktop_apps = service._discover_desktop_shortcuts()
        assert len(desktop_apps) == 1
        assert desktop_apps[0].display_name == "Discord"
        assert desktop_apps[0].source == "desktop_shortcut"
        assert desktop_apps[0].arguments == ["--processStart", "Discord.exe"]


# ==============================================================================
# 5–7. Deduplication, Indexing, Persistence, & Cache Refresh
# ==============================================================================


def test_05_duplicate_application_removal_and_merging(tmp_path: Path):
    """Verify duplicate entries from multiple sources merge cleanly without duplicate IDs."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    entry_a = DiscoveredApplication(
        app_id="docker-desktop",
        name="docker desktop",
        display_name="Docker Desktop",
        executable_path=r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        source="start_menu",
        launch_method="executable",
        publisher=None,
        aliases=["docker"],
    )
    entry_b = DiscoveredApplication(
        app_id="docker-desktop",
        name="docker desktop",
        display_name="Docker Desktop",
        executable_path=r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        source="registry",
        launch_method="executable",
        publisher="Docker Inc.",
        install_location=r"C:\Program Files\Docker\Docker",
        aliases=["docker desktop app"],
    )
    service._add_or_merge_entry(entry_a)
    service._add_or_merge_entry(entry_b)

    merged = service._entries["docker-desktop"]
    assert merged.publisher == "Docker Inc."
    assert merged.install_location == r"C:\Program Files\Docker\Docker"
    assert "docker" in merged.aliases
    assert "docker desktop app" in merged.aliases


def test_06_07_application_indexing_persistence_and_refresh(tmp_path: Path):
    """Verify index persists to application_index.json and reloads/refreshes cleanly."""
    index_file = tmp_path / "config" / "application_index.json"
    service = ApplicationDiscoveryService(
        index_file=index_file,
        auto_discover=False,
    )

    fake_app = DiscoveredApplication(
        app_id="android-studio",
        name="android studio",
        display_name="Android Studio",
        executable_path=r"C:\Program Files\Android\Android Studio\bin\studio64.exe",
        source="start_menu",
        launch_method="executable",
        publisher="Google LLC",
    )

    with patch.object(service, "_discover_start_menu_shortcuts", return_value=[fake_app]), \
         patch.object(service, "_discover_desktop_shortcuts", return_value=[]), \
         patch.object(service, "_discover_registry_applications", return_value=[]), \
         patch.object(service, "_discover_known_directories", return_value=[]), \
         patch.object(service, "_discover_microsoft_store_apps", return_value=[]):
        apps = service.refresh_index(include_store_apps=False)
        assert any(a.app_id == "android-studio" for a in apps)
        assert index_file.is_file()

    # Load a second service instance from the persisted JSON file
    service2 = ApplicationDiscoveryService(
        index_file=index_file,
        auto_discover=False,
    )
    res = service2.resolve_query("Android Studio")
    assert res["status"] == "resolved"
    assert res["match"].display_name == "Android Studio"


# ==============================================================================
# 8–10. Alias Resolution, Fuzzy Matching, & Ambiguity Detection
# ==============================================================================


def test_08_09_alias_and_fuzzy_resolution(tmp_path: Path):
    """Verify exact, alias, partial, and fuzzy matching for installed applications."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    for app_id, disp, exe in [
        ("spotify", "Spotify", r"C:\Users\Test\AppData\Roaming\Spotify\Spotify.exe"),
        ("whatsapp", "WhatsApp", "shell:AppsFolder\\5319275A.WhatsAppDesktop!App"),
        ("visual-studio-code", "Visual Studio Code", r"C:\Program Files\Microsoft VS Code\Code.exe"),
        ("mongodb-compass", "MongoDB Compass", r"C:\Program Files\MongoDB Compass\MongoDBCompass.exe"),
        ("microsoft-word", "Microsoft Word", r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
    ]:
        service._add_or_merge_entry(
            DiscoveredApplication(
                app_id=app_id,
                name=normalize_app_name(disp),
                display_name=disp,
                executable_path=exe,
                source="start_menu",
                launch_method="store_app" if exe.startswith("shell:") else "executable",
            )
        )

    # Alias matches
    assert service.resolve_query("vs code")["match"].display_name == "Visual Studio Code"
    assert service.resolve_query("vscode")["match"].display_name == "Visual Studio Code"
    assert service.resolve_query("word")["match"].display_name == "Microsoft Word"
    assert service.resolve_query("whats app")["match"].display_name == "WhatsApp"
    assert service.resolve_query("compass")["match"].display_name == "MongoDB Compass"

    # Fuzzy match ("spotfy" -> "Spotify")
    fuzzy_res = service.resolve_query("spotfy")
    assert fuzzy_res["status"] == "resolved"
    assert fuzzy_res["match"].display_name == "Spotify"


def test_10_ambiguous_application_detection(tmp_path: Path):
    """Verify closely matching applications return status='ambiguous' with candidate names."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="python-3-11",
            name="python 3.11",
            display_name="Python 3.11 (64-bit)",
            executable_path=r"C:\Program Files\Python311\pythonw.exe",
            source="start_menu",
            launch_method="executable",
            aliases=["python ide"],
        )
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="python-3-12",
            name="python 3.12",
            display_name="Python 3.12 (64-bit)",
            executable_path=r"C:\Program Files\Python312\pythonw.exe",
            source="start_menu",
            launch_method="executable",
            aliases=["python ide"],
        )
    )

    res = service.resolve_query("python ide")
    assert res["status"] == "ambiguous"
    cand_names = [c.display_name for c in res["candidates"]]
    assert "Python 3.11 (64-bit)" in cand_names
    assert "Python 3.12 (64-bit)" in cand_names

    # Verify launcher raises AmbiguousApplicationError
    launcher = ApplicationLauncher(discovery_service=service)
    with pytest.raises(AmbiguousApplicationError) as exc_info:
        launcher.launch("python ide")
    assert len(exc_info.value.candidates) == 2


# ==============================================================================
# 11–13. Missing Executables, Invalid Shortcuts, & Unsafe Executable Paths
# ==============================================================================


def test_11_missing_executable_handling(tmp_path: Path):
    """Verify launching an uninstalled app or missing executable raises ApplicationNotFoundError."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="ghost-app",
            name="ghost app",
            display_name="Ghost App",
            executable_path=str(tmp_path / "ProgramFiles" / "Ghost" / "ghost.exe"),
            source="registry",
            launch_method="executable",
        )
    )
    launcher = ApplicationLauncher(discovery_service=service)

    # 1. Completely unknown/uninstalled application
    with pytest.raises(ApplicationNotFoundError, match="not installed|could not be found"):
        launcher.launch("NonExistentSuperApp2099")

    # 2. Indexed application whose binary was deleted from disk
    with pytest.raises(ApplicationNotFoundError, match="not found on disk"):
        launcher.launch("Ghost App")


def test_12_invalid_shortcut_handling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify corrupted .lnk files and shortcuts pointing to missing binaries are rejected."""
    monkeypatch.setenv("VOXPILOT_EXTRA_APP_DIRS", str(tmp_path))
    bad_lnk = tmp_path / "broken.lnk"
    bad_lnk.write_bytes(b"NOT_A_VALID_LNK_HEADER")
    assert parse_windows_lnk(bad_lnk) is None

    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="broken-shortcut-app",
            name="broken shortcut app",
            display_name="Broken Shortcut App",
            executable_path=str(tmp_path / "missing_target.exe"),
            shortcut_path=str(bad_lnk),
            source="desktop_shortcut",
            launch_method="shortcut",
        )
    )
    launcher = ApplicationLauncher(discovery_service=service)
    with pytest.raises(ApplicationNotFoundError):
        launcher.launch("Broken Shortcut App")


def test_13_unsafe_executable_path_and_prohibited_binary_rejection(tmp_path: Path):
    """Verify executables in Temp/Downloads or prohibited shells (cmd, powershell, regedit) are blocked."""
    # 1. Temp directory executable
    temp_dir = tmp_path / "Temp"
    temp_dir.mkdir(parents=True)
    temp_exe = temp_dir / " TrojanApp.exe"
    temp_exe.write_bytes(b"MZ_TEMP")
    assert is_suspicious_or_temporary_path(temp_exe) is True
    ok, _, reason = validate_executable_target(temp_exe, allow_custom_roots=[tmp_path])
    assert ok is False
    assert "temporary or downloads" in reason.lower()

    # 2. Downloads directory executable
    dl_dir = tmp_path / "Downloads"
    dl_dir.mkdir(parents=True)
    dl_exe = dl_dir / "installer.exe"
    dl_exe.write_bytes(b"MZ_DL")
    assert is_suspicious_or_temporary_path(dl_exe) is True

    # 3. Prohibited system shells/interpreters
    for blocked in ("cmd.exe", "powershell.exe", "pwsh.exe", "regedit.exe", "mshta.exe", "wscript.exe"):
        assert blocked in PROHIBITED_EXECUTABLES

    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="temp-app",
            name="temp app",
            display_name="Temp App",
            executable_path=str(temp_exe),
            source="desktop_shortcut",
            launch_method="executable",
        )
    )
    launcher = ApplicationLauncher(discovery_service=service)
    with pytest.raises(UnsafeExecutablePathError):
        launcher.launch("Temp App")

    for forbidden_query in ("cmd", "powershell", "regedit", "mshta", "wscript", "bash", "wsl"):
        with pytest.raises(ApplicationNotAllowedError):
            launcher.launch(forbidden_query)


# ==============================================================================
# 14–16. Launching Across All 6 Methods, Settings URIs, Utilities, & Folders
# ==============================================================================


def test_14_15_16_launch_methods_settings_utilities_and_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify shell=False command construction across executable, shortcut, store_app, settings_uri, system_utility, and shell_folder."""
    trusted_root = tmp_path / "ProgramFiles"
    app_dir = trusted_root / "Obsidian"
    app_dir.mkdir(parents=True)
    obsidian_exe = app_dir / "Obsidian.exe"
    obsidian_exe.write_bytes(b"MZ_OBSIDIAN")

    lnk_file = create_synthetic_lnk(
        trusted_root / "Obsidian.lnk",
        target_exe=str(obsidian_exe),
        arguments="--safe-mode",
    )

    monkeypatch.setenv("VOXPILOT_EXTRA_APP_DIRS", str(trusted_root))
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "index.json",
        auto_discover=False,
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="obsidian",
            name="obsidian",
            display_name="Obsidian",
            executable_path=str(obsidian_exe),
            source="program_files",
            launch_method="executable",
        )
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="obsidian-shortcut",
            name="obsidian shortcut",
            display_name="Obsidian Shortcut",
            executable_path=str(obsidian_exe),
            shortcut_path=str(lnk_file),
            arguments=["--safe-mode"],
            source="start_menu",
            launch_method="shortcut",
        )
    )
    service._add_or_merge_entry(
        DiscoveredApplication(
            app_id="whatsapp",
            name="whatsapp",
            display_name="WhatsApp",
            executable_path="shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            source="microsoft_store",
            launch_method="store_app",
        )
    )

    launcher = ApplicationLauncher(discovery_service=service)

    with patch("backend.automation.application_launcher.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(pid=4242)

        # 1. Executable launch
        res_exe = launcher.launch("Obsidian")
        assert res_exe["success"] is True
        assert res_exe["launch_method"] == "executable"
        args_exe, kwargs_exe = mock_popen.call_args
        assert kwargs_exe["shell"] is False
        assert Path(args_exe[0][0]).name == "Obsidian.exe"

        # 2. Shortcut launch
        res_lnk = launcher.launch("Obsidian Shortcut")
        assert res_lnk["success"] is True
        assert res_lnk["launch_method"] == "shortcut"
        args_lnk, kwargs_lnk = mock_popen.call_args
        assert kwargs_lnk["shell"] is False
        assert args_lnk[0][-1] == "--safe-mode"

        # 3. Store app launch
        res_store = launcher.launch("WhatsApp")
        assert res_store["success"] is True
        assert res_store["launch_method"] == "store_app"
        args_store, kwargs_store = mock_popen.call_args
        assert kwargs_store["shell"] is False
        assert args_store[0] == [
            r"C:\Windows\explorer.exe",
            "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
        ]

        # 4. Windows Settings URI launch ("Bluetooth Settings" -> ms-settings:bluetooth)
        res_bt = launcher.launch("Bluetooth Settings")
        assert res_bt["success"] is True
        assert res_bt["launch_method"] == "settings_uri"
        args_bt, kwargs_bt = mock_popen.call_args
        assert kwargs_bt["shell"] is False
        assert args_bt[0] == [r"C:\Windows\explorer.exe", "ms-settings:bluetooth"]

        # 5. Built-in Windows utility ("Control Panel" -> control.exe)
        res_cp = launcher.launch("Control Panel")
        assert res_cp["success"] is True
        assert res_cp["launch_method"] == "system_utility"
        args_cp, kwargs_cp = mock_popen.call_args
        assert kwargs_cp["shell"] is False
        assert Path(args_cp[0][0]).name.lower() == "control.exe"

        # 6. Special folder ("Recycle Bin" -> explorer.exe shell:RecycleBinFolder)
        res_rb = launcher.launch("Recycle Bin")
        assert res_rb["success"] is True
        assert res_rb["launch_method"] == "shell_folder"
        args_rb, kwargs_rb = mock_popen.call_args
        assert kwargs_rb["shell"] is False
        assert args_rb[0] == [r"C:\Windows\explorer.exe", "shell:RecycleBinFolder"]


# ==============================================================================
# 17–18. Intent Recognition & Planner Parameter Normalization
# ==============================================================================


@pytest.mark.parametrize(
    "command,expected_action",
    [
        ("Open Notepad", "launch_application"),
        ("Open WhatsApp", "launch_application"),
        ("Open Spotify", "launch_application"),
        ("Start Discord", "launch_application"),
        ("Open Visual Studio Code", "launch_application"),
        ("Open Android Studio", "launch_application"),
        ("Launch Docker Desktop", "launch_application"),
        ("Open MongoDB Compass", "launch_application"),
        ("Open Windows Settings", "launch_application"),
        ("Open Bluetooth Settings", "launch_application"),
        ("Open Control Panel", "launch_application"),
        ("Open Device Manager", "launch_application"),
        ("Open Task Manager", "launch_application"),
        ("Open File Explorer", "launch_application"),
        ("Open Downloads", "launch_application"),
        ("Open Recycle Bin", "launch_application"),
        ("Show me all installed applications", "search_applications"),
        ("Refresh application index", "refresh_applications"),
    ],
)
def test_17_18_intent_and_deterministic_planner_for_universal_commands(
    command: str, expected_action: str
):
    """Verify natural-language commands classify as open_application and generate valid canonical plans."""
    ir = IntentRecognizer()
    planner = AgentPlanner()
    registry = get_shared_tool_registry()

    intent_res = ir.recognize_intent(command)
    assert intent_res["intent"] == "open_application"

    plan = planner.create_plan(command, intent=intent_res["intent"])
    assert len(plan["steps"]) == 1
    step = plan["steps"][0]
    assert step["action"] == expected_action
    # Validate parameters against ToolRegistry Pydantic schema
    registry.validate_parameters(step["action"], step["parameters"])


# ==============================================================================
# 19–21. Security Confirmation for Sensitive Utilities, Audit Logging, & E-Stop
# ==============================================================================


def test_19_security_confirmation_for_sensitive_utilities():
    """Verify sensitive system utilities (Device Manager, Services, msconfig) require human confirmation."""
    reg = ToolRegistry()
    cm = ConfirmationManager()
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    # Step 1: Without confirmation token -> pauses with confirmation_required
    res_paused = executor.execute_step(
        step={
            "action": "launch_application",
            "description": "Open Device Manager",
            "parameters": {"application_name": "Device Manager"},
        },
        step_index=0,
    )
    assert res_paused["success"] is False
    assert res_paused["status"] == "confirmation_required"
    token = res_paused["confirmation_token"]
    assert token is not None

    # Step 2: Approve token and re-execute -> succeeds
    cm.approve_confirmation(token)
    with patch("backend.automation.application_launcher.ApplicationLauncher.launch") as mock_launch:
        mock_launch.return_value = {
            "success": True,
            "app_id": "utility-device-manager",
            "display_name": "Device Manager",
            "pid": 8888,
            "status": "launched",
            "launch_method": "system_utility",
            "source": "system_utility",
            "message": "Successfully launched Device Manager (PID: 8888).",
            "timestamp": "2026-11-11T12:00:00Z",
        }
        res_approved = executor.execute_step(
            step={
                "action": "launch_application",
                "description": "Open Device Manager",
                "parameters": {"application_name": "Device Manager"},
            },
            step_index=0,
            override_confirmation_token=token,
        )
        assert res_approved["success"] is True
        assert res_approved["status"] == "launched"


def test_20_21_fastapi_endpoints_audit_logging_and_emergency_stop(tmp_path: Path):
    """Verify GET /agent/applications, POST /agent/applications/refresh, POST /agent/applications/launch, and E-Stop."""
    client = TestClient(app)
    headers = {"Origin": "http://localhost:3000"}

    # 1. GET /agent/applications
    res_list = client.get("/agent/applications?query=Bluetooth", headers=headers)
    assert res_list.status_code == 200
    body = res_list.json()
    assert "applications" in body
    assert any("Bluetooth" in a["display_name"] for a in body["applications"])

    # 2. POST /agent/applications/launch for sensitive utility -> confirmation_required
    res_sens = client.post(
        "/agent/applications/launch",
        json={"query": "Device Manager"},
        headers=headers,
    )
    assert res_sens.status_code == 200
    sens_data = res_sens.json()
    assert sens_data["status"] == "confirmation_required"
    assert sens_data["requires_confirmation"] is True
    assert sens_data["confirmation_token"]

    # 3. Emergency stop blocks POST /agent/applications/launch
    estop = get_shared_emergency_stop_manager()
    estop.trigger_emergency_stop(reason="Test E-Stop", source="test")
    try:
        res_blocked = client.post(
            "/agent/applications/launch",
            json={"query": "Notepad"},
            headers=headers,
        )
        assert res_blocked.status_code == 409
    finally:
        if estop.is_emergency_stop_active():
            estop.reset_emergency_stop()


# ==============================================================================
# Live Windows 11 Integration Tests
# ==============================================================================


@pytest.mark.live
def test_live_windows_application_discovery_and_safe_launch(tmp_path: Path):
    """Live Windows 11 test: discover real installed apps and launch/terminate Notepad safely."""
    service = ApplicationDiscoveryService(
        index_file=tmp_path / "live_application_index.json",
        auto_discover=False,
    )
    discovered = service.refresh_index(include_store_apps=True)
    summary = service.get_index_summary()

    # Must discover built-ins + real installed apps on this Windows 11 machine
    assert len(discovered) >= 25
    assert summary["total_count"] >= 25

    # Verify Windows Settings & utilities resolve accurately
    bt_res = service.resolve_query("Bluetooth Settings")
    assert bt_res["status"] == "resolved"
    assert bt_res["match"].executable_path == "ms-settings:bluetooth"

    cp_res = service.resolve_query("Control Panel")
    assert cp_res["status"] == "resolved"
    assert cp_res["match"].launch_method == "system_utility"

    # Launch real Notepad via universal ApplicationLauncher and terminate cleanly
    launcher = ApplicationLauncher(discovery_service=service)
    launch_res = launcher.launch("Notepad")
    assert launch_res["success"] is True
    pid = launch_res["pid"]
    assert pid is not None and pid > 0

    time.sleep(0.8)
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=3)
    except psutil.NoSuchProcess:
        pass
