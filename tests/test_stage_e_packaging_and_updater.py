"""
VoxPilot Stage E Regression Tests — Production Packaging, Installer, and Graceful Updater Shutdown.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch

import pytest

from backend.database.backup import DatabaseBackupManager
from backend.database.connection import init_db_sync, reset_db_connection
from backend.desktop.shell import (
    DesktopShellController,
    get_shared_desktop_controller,
    set_shared_desktop_controller,
)
from backend.paths import get_backups_dir, get_database_path, get_updates_dir
from backend.updater.update_service import (
    UpdateService,
    UpdateSettingsManager,
)
from backend.version import DEFAULT_APP_VERSION, get_app_version


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_fake_installer(version_label: str = "0.6.0") -> Tuple[bytes, str]:
    payload = f"MZ-FAKE-VOXPILOT-STAGEE-SETUP-v{version_label}\n".encode("utf-8") * 128
    digest = hashlib.sha256(payload).hexdigest().lower()
    return payload, digest


def _make_release_payload(
    *,
    tag_name: str = "v0.6.0",
    sha256_hex: str,
    asset_name: str = "VoxPilot-Setup.exe",
    download_url: str = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe",
    size: int = 8192,
) -> Dict[str, Any]:
    return {
        "tag_name": tag_name,
        "name": f"VoxPilot {tag_name}",
        "body": f"Release {tag_name}\nSHA-256: `{sha256_hex}`\n",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-15T12:00:00Z",
        "html_url": f"https://github.com/Thanush505/VoxPilot/releases/tag/{tag_name}",
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": download_url,
                "size": size,
            }
        ],
    }


def test_version_authority_is_consistent_across_all_packaging_manifests() -> None:
    canonical = (REPO_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    assert canonical == DEFAULT_APP_VERSION == get_app_version()

    pkg_json = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg_json["version"] == canonical

    pkg_lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert pkg_lock["version"] == canonical
    assert pkg_lock["packages"][""]["version"] == canonical

    from backend.desktop.shell import get_canonical_app_user_model_id

    assert get_canonical_app_user_model_id() == f"VoxPilotTeam.VoxPilot.DesktopAgent.{canonical}"

    version_info = (REPO_ROOT / "assets" / "version_info.txt").read_text(encoding="utf-8")
    assert f"StringStruct('FileVersion', '{canonical}.0')" in version_info
    assert f"StringStruct('ProductVersion', '{canonical}')" in version_info
    major, minor, patch = canonical.split(".")
    assert f"filevers=({major}, {minor}, {patch}, 0)" in version_info

    iss_text = (REPO_ROOT / "installer" / "VoxPilot.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion Trim(FileRead(VersionFileHandle))' in iss_text
    assert 'VoxPilotTeam.VoxPilot.DesktopAgent.{#MyAppVersion}' in iss_text


def test_pyinstaller_spec_configures_console_free_desktop_shell_and_backups() -> None:
    spec_text = (REPO_ROOT / "VoxPilot.spec").read_text(encoding="utf-8")
    assert re.search(r"console\s*=\s*False", spec_text) is not None
    for required_entry in (
        "'backend.desktop'",
        "'backend.desktop.shell'",
        "'backend.database.backup'",
        "'webview'",
        "'webview.platforms.edgechromium'",
        "'webview.platforms.winforms'",
        "'pythonnet'",
        "'clr_loader'",
        "collect_dynamic_libs('webview')",
        "collect_data_files('webview')",
    ):
        assert required_entry in spec_text, f"Missing {required_entry} in VoxPilot.spec"


def test_inno_setup_script_has_canonical_repo_url_shortcuts_and_silent_uninstall_guard() -> None:
    iss_text = (REPO_ROOT / "installer" / "VoxPilot.iss").read_text(encoding="utf-8")
    assert '#define MyAppURL "https://github.com/Thanush505/VoxPilot"' in iss_text
    assert 'Source: "..\\install_browser.bat"; DestDir: "{app}"' in iss_text
    assert 'Source: "..\\README_DIST.txt"; DestDir: "{app}"; DestName: "README.txt"' in iss_text
    assert "not UninstallSilent" in iss_text
    assert "[UninstallDelete]" not in iss_text


def test_trigger_install_creates_pre_update_backup_and_invokes_desktop_graceful_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_data_dir = tmp_path / "VoxPilotUserData"
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(user_data_dir))
    reset_db_connection()
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db_sync(f"sqlite:///{db_path}")

    updates_dir = get_updates_dir()
    updates_dir.mkdir(parents=True, exist_ok=True)
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")

    installer_bytes, sha = _make_fake_installer("0.6.0")
    installer_path = updates_dir / "VoxPilot-Setup-0.6.0.exe"
    installer_path.write_bytes(installer_bytes)

    svc = UpdateService(settings_manager=settings_mgr, updates_dir=updates_dir, installed_version="0.5.2")
    svc._state = "ready_to_install"
    svc._latest_version = "0.6.0"
    svc._expected_sha256 = sha
    svc._sha256_verified = True
    svc._downloaded_installer_path = installer_path

    shutdown_called = threading.Event()
    controller = MagicMock(spec=DesktopShellController)
    controller.host = "127.0.0.1"
    controller.port = 8765
    controller.request_shutdown.side_effect = lambda: shutdown_called.set()

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    previous_controller = get_shared_desktop_controller()
    set_shared_desktop_controller(controller)
    try:
        with patch("backend.updater.update_service.subprocess.Popen", return_value=mock_proc), patch(
            "os.kill"
        ), patch("os._exit"):
            result = svc.trigger_install(confirm=True, relaunch=True, shutdown_app=True)
            assert result["state"] == "installing"
            assert shutdown_called.wait(timeout=3.0)
            for t in threading.enumerate():
                if t.name == "VoxPilotUpdateShutdown" and t.is_alive():
                    t.join(timeout=6.0)
    finally:
        set_shared_desktop_controller(previous_controller)
        reset_db_connection()

    ps_script = (updates_dir / "run_voxpilot_updater.ps1").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8765/health" in ps_script

    backup_dirs = list(get_backups_dir().glob("voxpilot_backup_*pre_update_v0_6_0*"))
    assert len(backup_dirs) == 1
    verified = DatabaseBackupManager().verify_backup(backup_dirs[0])
    assert verified["database"]["integrity_check"] == "ok"


def test_local_release_check_download_verify_and_cancel_cleanup(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    updates_dir = tmp_path / "updates"
    svc = UpdateService(settings_manager=settings_mgr, updates_dir=updates_dir, installed_version="0.5.2")

    installer_bytes, digest = _make_fake_installer("0.6.0")
    dl_url = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe"
    payload = _make_release_payload(tag_name="v0.6.0", sha256_hex=digest, download_url=dl_url, size=len(installer_bytes))

    with patch.object(svc, "_fetch_json_url", return_value=(payload, {})):
        check_status = svc.check_for_updates(force=True)
    assert check_status["state"] == "update_available"
    assert check_status["update_available"] is True
    assert check_status["latest_version"] == "0.6.0"

    # Bad checksum must be rejected and deleted
    svc._expected_sha256 = "0" * 64

    class _FakeResp(io.BytesIO):
        def __init__(self, data: bytes, url: str) -> None:
            super().__init__(data)
            self._url = url
            self.headers = {"Content-Length": str(len(data))}

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    with patch("backend.updater.update_service.urllib.request.urlopen", return_value=_FakeResp(installer_bytes, dl_url)):
        bad_dl = svc.start_download(background=False)
    assert bad_dl["state"] == "failed"
    assert bad_dl["error_code"] == "checksum_mismatch"
    assert list(updates_dir.glob("*.exe")) == []

    # Valid checksum must verify cleanly and reach ready_to_install
    with patch.object(svc, "_fetch_json_url", return_value=(payload, {})):
        svc.check_for_updates(force=True)
    with patch("backend.updater.update_service.urllib.request.urlopen", return_value=_FakeResp(installer_bytes, dl_url)):
        ok_dl = svc.start_download(background=False)
    assert ok_dl["state"] == "ready_to_install"
    assert ok_dl["download"]["sha256_verified"] is True
    assert Path(ok_dl["download"]["installer_path"]).is_file()
