# VoxPilot Data Persistence, Security & Recovery Guide

## 1. Application Data Layout (`%LOCALAPPDATA%\VoxPilot`)

In packaged Windows desktop mode (`sys.frozen` or `VOXPILOT_FORCE_USER_DATA=1`), VoxPilot stores all writable user state exclusively under `%LOCALAPPDATA%\VoxPilot`, keeping the installation directory (`%LOCALAPPDATA%\Programs\VoxPilot` or PyInstaller `_MEIPASS`) read-only and free of mutable user data:

| Subdirectory / File | Canonical Resolver (`backend/paths.py`) | Contents |
|---|---|---|
| `data\voxpilot.db` | `get_database_path()` | SQLite database (WAL mode: `.db`, `.db-wal`, `.db-shm`) for conversations, executions, memories, preferences, profiles, provider metadata, and automations |
| `config\` | `get_config_dir()` | Non-secret JSON configuration (`voice_settings.json`, `notification_preferences.json`, `application_index.json`, `update_settings.json`) |
| `credentials\` | `get_credentials_dir()` | Windows DPAPI-encrypted BYOK keys (`byok_<profile>_<vendor>.dpapi.json`) and email credentials (`email_profiles\<profile>.json`) |
| `backups\` | `get_backups_dir()` | Timestamped SQLite-consistent snapshots + `backup_manifest.json` SHA-256 verification manifests |
| `logs\` | `get_log_dir()` | Secret-redacted application log (`voxpilot.log`) |
| `exports\` | `get_export_dir()` | Explicitly exported audit bundles |
| `screenshots\` | `get_screenshots_dir()` | Internal screenshot storage |
| `downloads\` | `get_downloads_dir()` | Internal download storage |
| `temp\voice\` | `get_voice_temp_dir()` | Ephemeral Piper TTS synthesis WAV files (cleaned up after playback) |
| `webview\` | `get_webview_storage_dir()` | Microsoft Edge WebView2 profile/cache storage |
| `updates\` | `get_updates_dir()` | Downloaded signed/verified update installers and detached updater script |

In local repository development mode (when neither `sys.frozen`, `VOXPILOT_FORCE_USER_DATA`, nor `VOXPILOT_USER_DATA_DIR` is set), runtime `data/`, `logs/`, and `exports/` remain local to the repository working tree, while `get_config_dir()` and `get_updates_dir()` resolve under `%LOCALAPPDATA%\VoxPilot` unless overridden by environment variables.

## 2. SQLite Configuration & Legacy Migrations

- Connections automatically configure `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`, and `PRAGMA busy_timeout = 5000`.
- `run_migrations_with_connection()` runs idempotently at startup without dropping tables or deleting rows.
- Legacy `ExecutionRecord.plan_json` entries (such as older string-element plans `["..."]` or scalar `ExecutionStepRecord.parameters_json` payloads) are normalized non-destructively in-place and on read via `normalize_plan_steps()`.

## 3. Windows DPAPI & Secret Hygiene

- API keys (`OpenAI`, `Anthropic`, `Gemini`) and email passwords are encrypted per-Windows-user with `CryptProtectData` (DPAPI) via atomic temp-file writes (`os.replace`) under `credentials/`, isolated per `profile_id`.
- `SQLite` stores only non-secret credential references (`byok:<profile>:<vendor>`). Raw secrets are never returned to the frontend or stored in `profile_provider_configs`.
- `SecretRedactingLogFilter` scrubs API keys (`sk-*`, `sk-ant-*`, `sk-proj-*`, `AIza*`), `Bearer` tokens, GitHub tokens, and `X-VoxPilot-Desktop-Token` headers from log records before disk emission.

## 4. Safe Backup & Manual Recovery

**Never copy a live `.db` file manually while VoxPilot is running** (doing so can omit uncommitted WAL frames or capture a torn page). Use `DatabaseBackupManager` or `POST /api/system/backups` instead:

```python
from backend.database.backup import DatabaseBackupManager

mgr = DatabaseBackupManager()
manifest = mgr.create_backup(label="manual_snapshot")
mgr.verify_backup(manifest["backup_dir"])
```

### Restoring from a Snapshot (Manual / Explicit Confirmation Only)

VoxPilot never restores or overwrites user data automatically. To restore manually:

1. Close the running VoxPilot desktop application completely.
2. Inspect available snapshots via `DatabaseBackupManager().list_backups()` or `%LOCALAPPDATA%\VoxPilot\backups\`.
3. Verify the target snapshot before restoring:

```python
from pathlib import Path
from backend.database.backup import DatabaseBackupManager

mgr = DatabaseBackupManager()
backup_dir = Path(r"C:\Users\<User>\AppData\Local\VoxPilot\backups\voxpilot_backup_<timestamp>")
mgr.verify_backup(backup_dir)

# Creates an automatic pre_restore_safety snapshot before restoring via SQLite Online Backup API
result = mgr.restore_backup(backup_dir, confirm_overwrite=True)
```

## 5. Upgrade & Uninstall Data Retention

- **Upgrades:** Inno Setup overwrites binaries in `%LOCALAPPDATA%\Programs\VoxPilot` only; `%LOCALAPPDATA%\VoxPilot` is untouched and schema migrations run non-destructively on next launch.
- **Uninstallation:** The Inno Setup uninstaller removes program binaries only and explicitly preserves `%LOCALAPPDATA%\VoxPilot` (database, encrypted DPAPI credentials, configuration, and backups).
