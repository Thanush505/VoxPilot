"""
Tests for Phase 5A: Wake-on-Startup.

Covers (with mocked Windows registry — never touches the real HKCU Run key):
- Startup status detection (enabled vs disabled vs missing Run key)
- Enabling and disabling startup idempotently
- Duplicate prevention (case-variant cleanup)
- Invalid executable path and missing directory handling
- PermissionError handling
- Packaged vs development-mode command construction
- FastAPI `/api/system/startup` GET and POST endpoints
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.automation.startup_manager import (
    RUN_KEY_PATH,
    STARTUP_VALUE_NAME,
    StartupConfigurationError,
    StartupPermissionError,
    WindowsStartupManager,
)
from backend.main import app


class _FakeKey:
    def __init__(self, store: dict[str, str], read_only: bool = False):
        self.store = store
        self.read_only = read_only

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, initial_values: dict[str, str] | None = None, key_exists: bool = True, fail_perm: bool = False):
        self.values: dict[str, str] = dict(initial_values or {})
        self.key_exists = key_exists
        self.fail_perm = fail_perm

    def OpenKey(self, root, subkey, reserved, access):
        if self.fail_perm:
            raise PermissionError("Access is denied")
        if not self.key_exists or subkey != RUN_KEY_PATH:
            raise FileNotFoundError("Run key missing")
        return _FakeKey(self.values)

    def CreateKeyEx(self, root, subkey, reserved, access):
        if self.fail_perm:
            raise PermissionError("Access is denied")
        self.key_exists = True
        return _FakeKey(self.values)

    def QueryValueEx(self, key: _FakeKey, name: str):
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], self.REG_SZ

    def SetValueEx(self, key: _FakeKey, name: str, reserved, reg_type, value: str):
        if self.fail_perm:
            raise PermissionError("Access is denied")
        key.store[name] = value

    def DeleteValue(self, key: _FakeKey, name: str):
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]

    def EnumValue(self, key: _FakeKey, index: int):
        keys = list(key.store.keys())
        if index >= len(keys):
            raise OSError("No more data is available")
        k = keys[index]
        return k, key.store[k], self.REG_SZ


class TestWindowsStartupManager:
    def test_status_detection_missing_run_key_and_enable_disable_dev_mode(self, tmp_path: Path):
        fake_py = tmp_path / "python.exe"
        fake_py.write_text("exe", encoding="utf-8")
        fake_launcher = tmp_path / "launcher.py"
        fake_launcher.write_text("print('start')", encoding="utf-8")

        reg = _FakeWinreg(key_exists=False)
        mgr = WindowsStartupManager(
            executable_path=fake_py,
            launcher_path=fake_launcher,
            frozen_override=False,
            registry_module=reg,
        )

        initial = mgr.get_status()
        assert initial.enabled is False
        assert initial.supported is True
        assert initial.mode == "development"
        assert initial.target_valid is True

        enabled_status = mgr.enable()
        assert enabled_status.enabled is True
        assert STARTUP_VALUE_NAME in reg.values
        assert str(fake_py.resolve()) in reg.values[STARTUP_VALUE_NAME]
        assert str(fake_launcher.resolve()) in reg.values[STARTUP_VALUE_NAME]

        # Calling enable again does not create duplicate entries
        mgr.enable()
        assert list(reg.values.keys()) == [STARTUP_VALUE_NAME]

        disabled_status = mgr.disable()
        assert disabled_status.enabled is False
        assert STARTUP_VALUE_NAME not in reg.values

        # Disabling again when already disabled is idempotent
        disabled_again = mgr.disable()
        assert disabled_again.enabled is False

    def test_packaged_mode_and_duplicate_cleanup(self, tmp_path: Path):
        fake_exe = tmp_path / "VoxPilot.exe"
        fake_exe.write_text("binary", encoding="utf-8")

        reg = _FakeWinreg(initial_values={"voxpilot": "C:\\old\\VoxPilot.exe", "OtherApp": "C:\\other.exe"})
        mgr = WindowsStartupManager(
            executable_path=fake_exe,
            frozen_override=True,
            registry_module=reg,
        )

        res = mgr.enable()
        assert res.enabled is True
        assert res.mode == "packaged"
        assert "voxpilot" not in reg.values
        assert reg.values[STARTUP_VALUE_NAME] == f'"{fake_exe.resolve()}"'
        assert reg.values["OtherApp"] == "C:\\other.exe"

    def test_invalid_executable_and_missing_directory_rejected(self, tmp_path: Path):
        missing_dir_exe = tmp_path / "missing_dir" / "VoxPilot.exe"
        reg = _FakeWinreg()
        mgr = WindowsStartupManager(
            executable_path=missing_dir_exe,
            frozen_override=True,
            registry_module=reg,
        )
        status = mgr.get_status()
        assert status.target_valid is False
        assert status.reason is not None

        with pytest.raises(StartupConfigurationError):
            mgr.enable()
        assert STARTUP_VALUE_NAME not in reg.values

        # Non-.exe in packaged mode is rejected
        bad_ext = tmp_path / "VoxPilot.bat"
        bad_ext.write_text("echo hi", encoding="utf-8")
        mgr_bad = WindowsStartupManager(
            executable_path=bad_ext,
            frozen_override=True,
            registry_module=reg,
        )
        with pytest.raises(StartupConfigurationError):
            mgr_bad.enable()

    def test_permission_error_handled_gracefully(self, tmp_path: Path):
        fake_exe = tmp_path / "VoxPilot.exe"
        fake_exe.write_text("binary", encoding="utf-8")
        reg = _FakeWinreg(fail_perm=True)
        mgr = WindowsStartupManager(
            executable_path=fake_exe,
            frozen_override=True,
            registry_module=reg,
        )

        status = mgr.get_status()
        assert status.enabled is False
        assert "permission" in (status.reason or "").lower()

        with pytest.raises(StartupPermissionError):
            mgr.enable()

        with pytest.raises(StartupPermissionError):
            mgr.disable()


class TestStartupSettingsAPI:
    def test_get_and_post_system_startup_endpoints(self, tmp_path: Path):
        fake_exe = tmp_path / "VoxPilot.exe"
        fake_exe.write_text("binary", encoding="utf-8")
        reg = _FakeWinreg()
        mgr = WindowsStartupManager(
            executable_path=fake_exe,
            frozen_override=True,
            registry_module=reg,
        )

        client = TestClient(app)
        with patch("backend.api.system.get_shared_startup_manager", return_value=mgr):
            r_get = client.get("/api/system/startup")
            assert r_get.status_code == 200
            assert r_get.json()["enabled"] is False
            assert r_get.json()["mode"] == "packaged"

            r_enable = client.post("/api/system/startup", json={"enabled": True})
            assert r_enable.status_code == 200
            assert r_enable.json()["enabled"] is True

            r_disable = client.post("/api/system/startup", json={"enabled": False})
            assert r_disable.status_code == 200
            assert r_disable.json()["enabled"] is False
