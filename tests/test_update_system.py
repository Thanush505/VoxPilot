"""
VoxPilot - Comprehensive Backend & External Updater Tests
Covers:
1. Centralized version retrieval and semantic version comparison
2. GitHub release metadata parsing (stable vs beta channels, drafts, SHA-256 extraction)
3. Update available vs already up-to-date detection
4. Invalid release metadata and missing installer asset handling
5. GitHub offline/unavailable and API rate-limit handling
6. Secure installer download, progress tracking, and mid-download cancellation
7. SHA-256 checksum verification and corrupted/tampered installer rejection
8. Unsafe download URL and path-traversal filename rejection
9. Persistent update settings (auto-check, channel, repo, Remind Me Later, auto-install disabled)
10. External Windows updater process orchestration and isolated v0.5.0 -> v0.6.0 upgrade verification
    with full %LOCALAPPDATA%\\VoxPilot user data preservation.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import time
import urllib.error
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.updater.update_service import (
    ChecksumVerificationError,
    InvalidInstallerAssetError,
    UntrustedDownloadUrlError,
    UpdateError,
    UpdateService,
    UpdateSettingsManager,
    discover_github_repo,
    extract_sha256_from_text,
    validate_download_url,
    validate_installer_filename,
)
from backend.updater.updater_runner import (
    build_inno_setup_command,
    compute_file_sha256,
    execute_external_update,
    generate_powershell_updater_script,
)
from backend.version import (
    InvalidVersionError,
    compare_versions,
    get_app_version,
    is_newer_version,
    parse_semver,
    try_parse_semver,
)


def _make_fake_installer_bytes(version_label: str = "0.6.0") -> Tuple[bytes, str]:
    content = f"MZ-FAKE-VOXPILOT-SETUP-INSTALLER-PAYLOAD-v{version_label}\n".encode("utf-8") * 64
    digest = hashlib.sha256(content).hexdigest().lower()
    return content, digest


def _make_github_release_payload(
    tag_name: str = "v0.6.0",
    sha256_hex: str = "a" * 64,
    asset_name: str = "VoxPilot-Setup.exe",
    download_url: str = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe",
    asset_size: int = 4096,
    draft: bool = False,
    prerelease: bool = False,
    include_asset: bool = True,
    digest_field: str | None = None,
) -> Dict[str, Any]:
    assets = []
    if include_asset:
        asset_obj: Dict[str, Any] = {
            "name": asset_name,
            "browser_download_url": download_url,
            "size": asset_size,
        }
        if digest_field is not None:
            asset_obj["digest"] = digest_field
        assets.append(asset_obj)

    return {
        "tag_name": tag_name,
        "name": f"VoxPilot {tag_name}",
        "body": f"### Release Highlights\n- Improved desktop agent\n\nSHA-256: `{sha256_hex}`\n",
        "draft": draft,
        "prerelease": prerelease,
        "published_at": "2026-11-11T12:00:00Z",
        "html_url": f"https://github.com/Thanush505/VoxPilot/releases/tag/{tag_name}",
        "assets": assets,
    }


class _FakeHttpResponse:
    def __init__(self, body: bytes, status: int = 200, url: str = "", headers: Dict[str, str] | None = None):
        self._stream = io.BytesIO(body)
        self.status = status
        self._url = url
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._stream.close()


# ==============================================================================
# 1. Version Retrieval & Semantic Version Comparison Tests
# ==============================================================================

def test_centralized_app_version_retrieval() -> None:
    version = get_app_version()
    parsed = parse_semver(version)
    assert version == "0.5.0"
    assert parsed.major == 0
    assert parsed.minor == 5
    assert parsed.patch == 0
    assert not parsed.is_prerelease


def test_semantic_version_parsing_and_comparison() -> None:
    assert compare_versions("0.5.0", "0.5.0") == 0
    assert compare_versions("v0.5.0", "0.5.0") == 0
    assert compare_versions("0.5.0", "0.6.0") == -1
    assert compare_versions("0.6.0", "0.5.9") == 1
    assert compare_versions("1.0.0-beta.1", "1.0.0") == -1
    assert compare_versions("1.0.0-beta.1", "1.0.0-beta.2") == -1

    assert is_newer_version("0.5.0", "0.5.0") is False
    assert is_newer_version("0.5.0", "0.4.9") is False
    assert is_newer_version("0.5.0", "0.5.1") is True
    assert is_newer_version("0.5.0", "0.6.0") is True
    # Pre-releases ignored on stable channel, allowed on beta channel
    assert is_newer_version("0.5.0", "0.6.0-beta.1", allow_prerelease=False) is False
    assert is_newer_version("0.5.0", "0.6.0-beta.1", allow_prerelease=True) is True


@pytest.mark.parametrize("bad_version", ["", "   ", "latest", "1.2", "v1.2.3.4", "abc.def.ghi"])
def test_invalid_semantic_version_rejected(bad_version: str) -> None:
    with pytest.raises(InvalidVersionError):
        parse_semver(bad_version)
    assert try_parse_semver(bad_version) is None
    assert is_newer_version("0.5.0", bad_version) is False


def test_discover_github_repo_defaults_to_thanush505_voxpilot() -> None:
    repo = discover_github_repo()
    assert repo == "Thanush505/VoxPilot"


# ==============================================================================
# 2. URL & Filename Security Validation Tests
# ==============================================================================

def test_validate_download_url_allows_official_github_https() -> None:
    urls = [
        "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe",
        "https://objects.githubusercontent.com/github-production-release-asset/12345/VoxPilot-Setup.exe",
        "https://api.github.com/repos/Thanush505/VoxPilot/releases/latest",
    ]
    for u in urls:
        assert validate_download_url(u) == u


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "",
        "http://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe",
        "ftp://github.com/Thanush505/VoxPilot/VoxPilot-Setup.exe",
        "https://evil-attacker.example.com/VoxPilot-Setup.exe",
        "https://127.0.0.1:8000/VoxPilot-Setup.exe",
        "https://github.com/Thanush505/VoxPilot/../../etc/passwd",
        "file:///C:/Windows/System32/cmd.exe",
    ],
)
def test_validate_download_url_rejects_untrusted_or_insecure_urls(unsafe_url: str) -> None:
    with pytest.raises(UntrustedDownloadUrlError):
        validate_download_url(unsafe_url)


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "../VoxPilot-Setup.exe",
        "..\\VoxPilot-Setup.exe",
        "subdir/VoxPilot-Setup.exe",
        "C:\\VoxPilot-Setup.exe",
        "VoxPilot-Setup.bat",
        "VoxPilot-Setup.ps1",
        "VoxPilot-Setup.zip",
        ".exe",
    ],
)
def test_validate_installer_filename_rejects_unsafe_names(bad_name: str) -> None:
    with pytest.raises(InvalidInstallerAssetError):
        validate_installer_filename(bad_name)


def test_extract_sha256_from_release_text_formats() -> None:
    expected = "0036bc257fabca414e9f0aa0b5a91554c04c292272b09a9d04fe50154285dae0"
    notes_table = f"| File | SHA-256 |\n|---|---|\n| VoxPilot-Setup.exe | `{expected.upper()}` |"
    assert extract_sha256_from_text(notes_table) == expected

    sha256sums = f"{expected} *VoxPilot-Setup.exe\n" + ("b" * 64) + " *other.zip\n"
    assert extract_sha256_from_text(sha256sums, "VoxPilot-Setup.exe") == expected

    single_hash = f"{expected}\n"
    assert extract_sha256_from_text(single_hash) == expected


# ==============================================================================
# 3. Release Checking, Rate Limit, Offline, and Metadata Validation Tests
# ==============================================================================

def test_update_check_detects_new_version_available(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    _, digest = _make_fake_installer_bytes("0.6.0")
    release_payload = _make_github_release_payload(tag_name="v0.6.0", sha256_hex=digest)

    with patch.object(service, "_fetch_json_url", return_value=(release_payload, {})):
        status = service.check_for_updates(force=True)

    assert status["state"] == "update_available"
    assert status["update_available"] is True
    assert status["installed_version"] == "0.5.0"
    assert status["latest_version"] == "0.6.0"
    assert status["release"]["title"] == "VoxPilot v0.6.0"
    assert status["release"]["expected_sha256"] == digest
    assert status["release"]["installer_name"] == "VoxPilot-Setup.exe"


def test_update_check_when_already_up_to_date_or_newer(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    # Same version v0.5.0
    same_payload = _make_github_release_payload(tag_name="v0.5.0")
    with patch.object(service, "_fetch_json_url", return_value=(same_payload, {})):
        status = service.check_for_updates(force=True)
    assert status["state"] == "up_to_date"
    assert status["update_available"] is False

    # Older version on GitHub v0.4.2
    older_payload = _make_github_release_payload(tag_name="v0.4.2")
    with patch.object(service, "_fetch_json_url", return_value=(older_payload, {})):
        status_older = service.check_for_updates(force=True)
    assert status_older["state"] == "up_to_date"
    assert status_older["update_available"] is False


def test_update_check_handles_invalid_release_metadata(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    bad_payload = _make_github_release_payload(tag_name="not-a-valid-semver")
    with patch.object(service, "_fetch_json_url", return_value=(bad_payload, {})):
        status = service.check_for_updates(force=True)

    assert status["state"] == "failed"
    assert status["update_available"] is False
    assert status["error_code"] == "invalid_release_version"


def test_update_check_handles_missing_installer_asset(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    no_asset_payload = _make_github_release_payload(tag_name="v0.6.0", include_asset=False)
    with patch.object(service, "_fetch_json_url", return_value=(no_asset_payload, {})):
        status = service.check_for_updates(force=True)

    assert status["state"] == "failed"
    assert status["update_available"] is False
    assert status["error_code"] == "missing_installer_asset"


def test_update_check_handles_github_offline_and_rate_limits(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )

    # 1. Offline / DNS failure
    with patch.object(
        service,
        "_fetch_json_url",
        side_effect=urllib.error.URLError("getaddrinfo failed"),
    ):
        offline_status = service.check_for_updates(force=True)
    assert offline_status["state"] == "offline"
    assert offline_status["error_code"] == "network_unavailable"

    # 2. GitHub Rate Limit (HTTP 403 with X-RateLimit-Remaining: 0)
    hdrs = MagicMock()
    hdrs.get = lambda k, default=None: "0" if k == "X-RateLimit-Remaining" else default
    rate_err = urllib.error.HTTPError(
        url="https://api.github.com/repos/Thanush505/VoxPilot/releases/latest",
        code=403,
        msg="Forbidden",
        hdrs=hdrs,
        fp=None,
    )
    with patch.object(service, "_fetch_json_url", side_effect=rate_err):
        rl_status = service.check_for_updates(force=True)
    assert rl_status["state"] == "failed"
    assert rl_status["error_code"] == "rate_limit_exceeded"


# ==============================================================================
# 4. Secure Download, Checksum Verification, Cancellation & Failure Tests
# ==============================================================================

def test_download_and_sha256_verification_success(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    installer_bytes, expected_digest = _make_fake_installer_bytes("0.6.0")
    dl_url = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe"
    release_payload = _make_github_release_payload(
        tag_name="v0.6.0",
        sha256_hex=expected_digest,
        download_url=dl_url,
        asset_size=len(installer_bytes),
    )

    with patch.object(service, "_fetch_json_url", return_value=(release_payload, {})):
        service.check_for_updates(force=True)

    fake_resp = _FakeHttpResponse(installer_bytes, status=200, url=dl_url)
    with patch("urllib.request.urlopen", return_value=fake_resp), patch(
        "backend.updater.update_service.inspect_authenticode_signature",
        return_value={
            "signature_checked": True,
            "signature_status": "NotSigned",
            "signature_valid": False,
            "signer_subject": None,
            "integrity_verification": "SHA-256 checksum verified against official release metadata",
            "publisher_verification_note": "Package integrity verified via official SHA-256 checksum.",
        },
    ):
        status = service.start_download(background=False)

    assert status["state"] == "ready_to_install"
    assert status["download"]["sha256_verified"] is True
    assert status["download"]["computed_sha256"] == expected_digest
    assert status["download"]["progress_pct"] == 100.0
    saved_path = Path(status["download"]["installer_path"])
    assert saved_path.is_file()
    assert saved_path.read_bytes() == installer_bytes


def test_download_rejects_mismatched_sha256_and_deletes_corrupted_file(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    updates_dir = tmp_path / "updates"
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=updates_dir,
        installed_version="0.5.0",
    )
    installer_bytes, _ = _make_fake_installer_bytes("0.6.0")
    wrong_digest = "f" * 64
    dl_url = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe"
    release_payload = _make_github_release_payload(
        tag_name="v0.6.0",
        sha256_hex=wrong_digest,
        download_url=dl_url,
        asset_size=len(installer_bytes),
    )

    with patch.object(service, "_fetch_json_url", return_value=(release_payload, {})):
        service.check_for_updates(force=True)

    fake_resp = _FakeHttpResponse(installer_bytes, status=200, url=dl_url)
    with patch("urllib.request.urlopen", return_value=fake_resp):
        status = service.start_download(background=False)

    assert status["state"] == "failed"
    assert status["error_code"] == "checksum_mismatch"
    assert status["download"]["sha256_verified"] is False
    assert status["download"]["installer_path"] is None
    # Verify neither .part nor .exe file remains in updates_dir
    assert list(updates_dir.glob("*.exe")) == []
    assert list(updates_dir.glob("*.part")) == []


def test_download_cancellation_cleans_up_partial_file(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    updates_dir = tmp_path / "updates"
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=updates_dir,
        installed_version="0.5.0",
    )
    installer_bytes, expected_digest = _make_fake_installer_bytes("0.6.0")
    dl_url = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe"
    release_payload = _make_github_release_payload(
        tag_name="v0.6.0",
        sha256_hex=expected_digest,
        download_url=dl_url,
        asset_size=len(installer_bytes),
    )

    with patch.object(service, "_fetch_json_url", return_value=(release_payload, {})):
        service.check_for_updates(force=True)

    class _SlowCancelResponse(_FakeHttpResponse):
        def read(self, size: int = -1) -> bytes:
            # Trigger cancellation right after first chunk read
            service._cancel_event.set()
            return super().read(1024)

    slow_resp = _SlowCancelResponse(installer_bytes, status=200, url=dl_url)
    with patch("urllib.request.urlopen", return_value=slow_resp):
        status = service.start_download(background=False)

    assert status["state"] == "update_available"
    assert status["download"]["sha256_verified"] is False
    assert list(updates_dir.glob("*.part")) == []
    assert list(updates_dir.glob("*.exe")) == []


def test_download_network_failure_handled_gracefully(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    installer_bytes, expected_digest = _make_fake_installer_bytes("0.6.0")
    dl_url = "https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe"
    release_payload = _make_github_release_payload(
        tag_name="v0.6.0",
        sha256_hex=expected_digest,
        download_url=dl_url,
        asset_size=len(installer_bytes),
    )

    with patch.object(service, "_fetch_json_url", return_value=(release_payload, {})):
        service.check_for_updates(force=True)

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection reset")):
        status = service.start_download(background=False)

    assert status["state"] == "failed"
    assert status["error_code"] == "download_failed"


# ==============================================================================
# 5. Persistent Update Settings & FastAPI Endpoints Tests
# ==============================================================================

def test_update_settings_persistence_and_enforced_no_auto_install(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config" / "update_settings.json"
    mgr = UpdateSettingsManager(config_path=cfg_file)
    initial = mgr.load()
    assert initial["auto_check_enabled"] is True
    assert initial["channel"] == "stable"
    assert initial["auto_install_enabled"] is False

    saved = mgr.save(
        {
            "auto_check_enabled": False,
            "channel": "beta",
            "dismissed_version": "0.6.0",
            "auto_install_enabled": True,  # Must be ignored and forced to False
        }
    )
    assert saved["auto_check_enabled"] is False
    assert saved["channel"] == "beta"
    assert saved["dismissed_version"] == "0.6.0"
    assert saved["auto_install_enabled"] is False

    reloaded = mgr.load()
    assert reloaded == saved


def test_fastapi_update_and_version_endpoints(tmp_path: Path) -> None:
    settings_mgr = UpdateSettingsManager(config_path=tmp_path / "update_settings.json")
    test_service = UpdateService(
        settings_manager=settings_mgr,
        updates_dir=tmp_path / "updates",
        installed_version="0.5.0",
    )
    installer_bytes, expected_digest = _make_fake_installer_bytes("0.6.0")
    release_payload = _make_github_release_payload(
        tag_name="v0.6.0",
        sha256_hex=expected_digest,
        asset_size=len(installer_bytes),
    )

    with patch("backend.api.updates.get_shared_update_service", return_value=test_service):
        client = TestClient(app)

        # 1. GET /api/version and GET /agent/version
        ver_res = client.get("/api/version")
        assert ver_res.status_code == 200
        assert ver_res.json()["installed_version"] == "0.5.0"

        agent_ver_res = client.get("/agent/version")
        assert agent_ver_res.status_code == 200
        assert agent_ver_res.json()["installed_version"] == "0.5.0"

        # 2. POST /api/updates/check
        with patch.object(test_service, "_fetch_json_url", return_value=(release_payload, {})):
            chk_res = client.post("/api/updates/check", json={"force": True})
        assert chk_res.status_code == 200
        assert chk_res.json()["update_available"] is True
        assert chk_res.json()["latest_version"] == "0.6.0"

        # 3. POST /api/updates/dismiss ("Remind Me Later")
        dis_res = client.post("/api/updates/dismiss", json={"version": "0.6.0"})
        assert dis_res.status_code == 200
        assert dis_res.json()["notification_dismissed"] is True

        # 4. POST /api/updates/settings
        set_res = client.post(
            "/api/updates/settings",
            json={"auto_check_enabled": False, "channel": "beta"},
        )
        assert set_res.status_code == 200
        assert set_res.json()["settings"]["auto_check_enabled"] is False
        assert set_res.json()["settings"]["channel"] == "beta"

        # 5. POST /api/updates/install without confirm=True must fail with 400
        bad_install = client.post("/api/updates/install", json={"confirm": False})
        assert bad_install.status_code == 400


# ==============================================================================
# 6. Isolated External Updater Process & Data Preservation Verification
# ==============================================================================

def test_external_updater_simulated_upgrade_and_user_data_preservation(tmp_path: Path) -> None:
    """Simulate a full v0.5.0 -> v0.6.0 upgrade in an isolated environment:
    - Creates a fake v0.5.0 installation directory and %LOCALAPPDATA%\\VoxPilot user data
      (SQLite database, voice_settings.json, update_settings.json, logs, exports).
    - Runs execute_external_update with a verified mock installer script/executable.
    - Verifies the application binary is upgraded to v0.6.0 while all user data files remain intact.
    - Verifies that an unverified or tampered installer is strictly rejected before execution.
    """
    install_dir = tmp_path / "Programs" / "VoxPilot"
    install_dir.mkdir(parents=True)
    version_file = install_dir / "VERSION"
    version_file.write_text("0.5.0\n", encoding="utf-8")

    # Populate simulated %LOCALAPPDATA%\VoxPilot user data
    user_data_dir = tmp_path / "LocalAppData" / "VoxPilot"
    db_file = user_data_dir / "data" / "voxpilot.db"
    voice_cfg = user_data_dir / "config" / "voice_settings.json"
    update_cfg = user_data_dir / "config" / "update_settings.json"
    log_file = user_data_dir / "logs" / "voxpilot.log"
    export_file = user_data_dir / "exports" / "audit_2026.json"
    updates_dir = user_data_dir / "updates"

    for p, content in [
        (db_file, "SQLITE_USER_CONVERSATION_AND_AUDIT_DB_V050"),
        (voice_cfg, '{"shortcut": "Ctrl+Alt+V", "tts_enabled": true}'),
        (update_cfg, '{"auto_check_enabled": true, "channel": "stable"}'),
        (log_file, "[2026-11-11] INFO: User session log entry\n"),
        (export_file, '{"records": [{"id": 1}]}'),
    ]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    updates_dir.mkdir(parents=True, exist_ok=True)
    installer_path = updates_dir / "VoxPilot-Setup-0.6.0.exe"
    installer_bytes, installer_sha256 = _make_fake_installer_bytes("0.6.0")
    installer_path.write_bytes(installer_bytes)
    result_file = updates_dir / "update_install_result.json"

    # Case A: Tampered / mismatched SHA-256 must be rejected before running subprocess
    tampered_res = execute_external_update(
        installer_path=installer_path,
        expected_sha256="0" * 64,
        target_version="0.6.0",
        result_file=result_file,
        install_dir=install_dir,
        relaunch=False,
    )
    assert tampered_res["status"] == "failed"
    assert tampered_res["exit_code"] == -3
    assert version_file.read_text(encoding="utf-8").strip() == "0.5.0"

    # Case B: Verified SHA-256 installer upgrades VERSION to 0.6.0 and preserves all user data
    def _mock_installer_run(cmd, **kwargs):
        assert str(installer_path) == cmd[0]
        assert "/SILENT" in cmd
        assert f"/DIR={install_dir}" in cmd
        # Simulate Inno Setup upgrading VERSION in install_dir without touching user_data_dir
        version_file.write_text("0.6.0\n", encoding="utf-8")
        completed = MagicMock()
        completed.returncode = 0
        return completed

    with patch("backend.updater.updater_runner.subprocess.run", side_effect=_mock_installer_run):
        success_res = execute_external_update(
            installer_path=installer_path,
            expected_sha256=installer_sha256,
            target_version="0.6.0",
            result_file=result_file,
            install_dir=install_dir,
            relaunch=False,
        )

    assert success_res["status"] == "completed"
    assert success_res["exit_code"] == 0
    assert version_file.read_text(encoding="utf-8").strip() == "0.6.0"

    # Verify all %LOCALAPPDATA%\VoxPilot user data was preserved untouched
    assert db_file.read_text(encoding="utf-8") == "SQLITE_USER_CONVERSATION_AND_AUDIT_DB_V050"
    assert voice_cfg.read_text(encoding="utf-8") == '{"shortcut": "Ctrl+Alt+V", "tts_enabled": true}'
    assert update_cfg.read_text(encoding="utf-8") == '{"auto_check_enabled": true, "channel": "stable"}'
    assert log_file.read_text(encoding="utf-8") == "[2026-11-11] INFO: User session log entry\n"
    assert export_file.read_text(encoding="utf-8") == '{"records": [{"id": 1}]}'

