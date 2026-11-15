# VoxPilot — GitHub Updates & Recovery Guide

VoxPilot uses a security-hardened, externalized Windows update pipeline (`backend/updater/update_service.py` and `backend/updater/updater_runner.py`) backed by official GitHub Releases (`https://github.com/Thanush505/VoxPilot`).

---

## 1. Update Architecture & Security Controls

1. **Release Source:** Configured GitHub repository (`Thanush505/VoxPilot` by default, HTTPS-only).
   - **Trusted Release API Hosts:** `api.github.com`
   - **Trusted Download Hosts:** `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `github-releases.githubusercontent.com`
   - Releases marked `draft: true` are ignored; prereleases are ignored on the `stable` channel unless `channel: "beta"` is explicitly selected.
2. **Version Comparison:** Semantic versioning via `backend/version.py` (`parse_semver`, `is_newer_version`). Downgrades and identical versions are reported as `up_to_date`.
3. **Mandatory SHA-256 Verification:**
   - The 64-hex SHA-256 digest is extracted from the release asset metadata (`digest`), `SHA256SUMS.txt` release asset, or release notes body.
   - Downloads without a valid SHA-256 checksum are blocked before transfer begins (`missing_checksum`).
   - Downloads stream into `%LOCALAPPDATA%\VoxPilot\updates\*.part` (supporting mid-download cancellation) and are hashed on disk before renaming to the final `.exe`. Any mismatch (`checksum_mismatch`) or truncation (`incomplete_download`) deletes the file immediately.
4. **Explicit User Confirmation Required:**
   - Automatic background installation is disabled.
   - `POST /api/updates/install` requires explicit `confirm=true` and originates from a trusted desktop session / loopback request.
   - Before spawning the external updater, `UpdateService.trigger_install()` re-verifies the on-disk SHA-256 hash.

---

## 2. Pre-Update Backup & Graceful Desktop Shell Shutdown

When `UpdateService.trigger_install()` runs against an initialized user data directory:

1. **Verified Pre-Update Snapshot:** Calls `DatabaseBackupManager().create_backup(label=f"pre_update_v{target_version}")` to save a verified SQLite Online Backup (`PRAGMA integrity_check == ok`) plus config JSON and DPAPI credentials inside `%LOCALAPPDATA%\VoxPilot\backups\`.
2. **Detached External PowerShell Updater:** Generates `%LOCALAPPDATA%\VoxPilot\updates\run_voxpilot_updater.ps1` and launches it as a detached hidden PowerShell process (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`).
3. **Graceful Desktop Shell Shutdown:** After the HTTP response returns, the shutdown thread resolves the active `DesktopShellController` via `get_shared_desktop_controller()` and calls `controller.request_shutdown()`:
   - Signals closing state and suppresses idle/active UI timers
   - Destroys the Edge WebView2 window
   - Stops `ManagedUvicornServer` and runs FastAPI lifespan shutdown (voice workers, hotkey hooks, database cleanup)
   - Allows `launcher.py` to release the Windows Named Mutex (`SingleInstanceGuard`) cleanly
   - Falls back to `SIGTERM` / `os._exit(0)` only if the process does not exit within the grace window.

---

## 3. External Updater Execution & Health Verification

The detached PowerShell helper (`run_voxpilot_updater.ps1`):
1. Waits for the old `VoxPilot.exe` PID to exit completely (releasing all file handles).
2. Re-verifies the downloaded installer's SHA-256 checksum.
3. Executes `VoxPilot-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS`.
4. Optionally relaunches `VoxPilot.exe` and polls the backend `/health` endpoint to verify the expected target version is active.
5. Writes a structured status report to `%LOCALAPPDATA%\VoxPilot\updates\update_install_result.json` (and session logs in `%LOCALAPPDATA%\VoxPilot\logs\`), surfaced in the UI on next launch.
6. Preserves `%LOCALAPPDATA%\VoxPilot` unconditionally; rollback is possible by running a prior `VoxPilot-Setup.exe` release and restoring any snapshot via `DatabaseBackupManager.restore_backup(..., confirm_overwrite=True)` (`docs/DATA_PERSISTENCE_AND_RECOVERY.md`).
