"""
VoxPilot - Unit Tests for Runtime Path Resolution
Verifies paths in development mode, frozen PyInstaller mode (mocked),
and environment variable override configurations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

from backend.paths import (
    is_frozen,
    get_project_root,
    get_bundle_dir,
    get_executable_dir,
    get_user_data_dir,
    get_database_path,
    get_export_dir,
    get_log_dir,
    get_config_dir,
    get_voice_temp_dir,
    get_frontend_dist_dir,
    get_piper_model_path,
    get_app_icon_path,
    ensure_user_directories,
)


def test_is_frozen_default(monkeypatch):
    """By default in test suite, application is not frozen."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not is_frozen()


def test_is_frozen_mocked(monkeypatch):
    """When sys.frozen is set, is_frozen returns True."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert is_frozen()


def test_get_project_root():
    """Verify get_project_root returns the VoxPilot repository root."""
    root = get_project_root()
    assert (root / "backend").is_dir()
    assert (root / "requirements").is_dir()


def test_get_bundle_dir_dev(monkeypatch):
    """In development, get_bundle_dir matches get_project_root."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert get_bundle_dir() == get_project_root()


def test_get_bundle_dir_frozen_meipass(monkeypatch, tmp_path):
    """In frozen mode with _MEIPASS, returns _MEIPASS path."""
    fake_meipass = tmp_path / "_MEIPASS_123"
    fake_meipass.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

    assert get_bundle_dir() == fake_meipass.resolve()


def test_get_executable_dir_frozen(monkeypatch, tmp_path):
    """In frozen mode, get_executable_dir returns directory of sys.executable."""
    fake_exe = tmp_path / "app" / "VoxPilot.exe"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

    assert get_executable_dir() == fake_exe.parent.resolve()


def test_get_user_data_dir_env_override(monkeypatch, tmp_path):
    """VOXPILOT_USER_DATA_DIR environment variable takes precedence."""
    custom_dir = tmp_path / "custom_voxpilot_data"
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(custom_dir))

    assert get_user_data_dir() == custom_dir.resolve()
    assert custom_dir.exists()


def test_get_user_data_dir_frozen(monkeypatch, tmp_path):
    """In frozen mode without override, uses LOCALAPPDATA\\VoxPilot."""
    monkeypatch.delenv("VOXPILOT_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    expected = fake_localappdata / "VoxPilot"
    assert get_user_data_dir() == expected.resolve()
    assert expected.exists()


def test_get_database_path_env_override(monkeypatch, tmp_path):
    """VOXPILOT_DB_PATH overrides default database path."""
    custom_db = tmp_path / "test_override.db"
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(custom_db))

    assert get_database_path() == custom_db.resolve()


def test_get_database_path_frozen(monkeypatch, tmp_path):
    """In frozen mode, database path resides in user-data data directory."""
    monkeypatch.delenv("VOXPILOT_DB_PATH", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    expected = fake_localappdata / "VoxPilot" / "data" / "voxpilot.db"
    assert get_database_path() == expected.resolve()
    assert expected.parent.exists()


def test_get_export_dir_frozen(monkeypatch, tmp_path):
    """In frozen mode, exports reside in user-data exports directory."""
    monkeypatch.delenv("VOXPILOT_EXPORT_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    expected = fake_localappdata / "VoxPilot" / "exports"
    assert get_export_dir() == expected.resolve()
    assert expected.exists()


def test_get_log_dir_frozen(monkeypatch, tmp_path):
    """In frozen mode, logs reside in user-data logs directory."""
    monkeypatch.delenv("VOXPILOT_LOG_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    expected = fake_localappdata / "VoxPilot" / "logs"
    assert get_log_dir() == expected.resolve()
    assert expected.exists()


def test_get_config_dir_frozen(monkeypatch, tmp_path):
    """In frozen mode, config resides in user-data config directory."""
    monkeypatch.delenv("VOXPILOT_CONFIG_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    expected = fake_localappdata / "VoxPilot" / "config"
    assert get_config_dir() == expected.resolve()
    assert expected.exists()


def test_get_frontend_dist_dir_frozen_discovery(monkeypatch, tmp_path):
    """In frozen mode, discovers frontend/dist in bundle directory."""
    bundle_dir = tmp_path / "bundle"
    dist_dir = bundle_dir / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html></html>")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)
    monkeypatch.delenv("VOXPILOT_FRONTEND_DIST", raising=False)

    assert get_frontend_dist_dir() == dist_dir.resolve()


def test_get_piper_model_path_env_override(monkeypatch, tmp_path):
    """VOXPILOT_PIPER_MODEL overrides default model location."""
    fake_model = tmp_path / "my_model.onnx"
    fake_model.touch()
    monkeypatch.setenv("VOXPILOT_PIPER_MODEL", str(fake_model))

    assert get_piper_model_path() == fake_model.resolve()


def test_ensure_user_directories(monkeypatch, tmp_path):
    """ensure_user_directories creates all required subdirectories."""
    custom_root = tmp_path / "user_data"
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(custom_root))

    ensure_user_directories()
    for sub in ("data", "logs", "exports", "screenshots", "downloads", "config", "temp/voice"):
        assert (custom_root / sub).is_dir()


def test_get_app_icon_path_exists_and_valid():
    """Verify get_app_icon_path resolves to the official multi-size voxpilot.ico."""
    icon_path = get_app_icon_path()
    assert icon_path.exists()
    assert icon_path.name == "voxpilot.ico"
    assert icon_path.stat().st_size > 10_000


