"""
VoxPilot - Stage D Tests: Application Data Paths, SQLite Compatibility, Backup & Security Hardening
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.agent.graph import VoxPilotAgent
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import ToolRegistry
from backend.ai.email_assistant import DPAPICredentialStore
from backend.ai.providers.base import ProviderConfigurationError
from backend.ai.providers.credentials import (
    SecretRedactingLogFilter,
    WindowsDPAPIProviderCredentialStore,
    sanitize_secret,
)
from backend.api.agent import clear_executions, set_agent
from backend.automation.confirmation_manager import ConfirmationManager
from backend.database.backup import (
    BackupAlreadyExistsError,
    BackupRestoreConfirmationError,
    BackupVerificationError,
    DatabaseBackupManager,
)
from backend.database.connection import (
    get_sync_engine,
    init_db_sync,
    reset_db_connection,
    run_migrations_with_connection,
)
from backend.database.repository import DatabaseRepository
from backend.desktop.shell import (
    DesktopShellController,
    get_shared_desktop_controller,
    set_shared_desktop_controller,
)
from backend.main import app
from backend.paths import (
    ensure_user_directories,
    get_backups_dir,
    get_config_dir,
    get_credentials_dir,
    get_database_path,
    get_downloads_dir,
    get_log_dir,
    get_runtime_paths_summary,
    get_screenshots_dir,
    get_updates_dir,
    get_user_data_dir,
    get_voice_temp_dir,
    get_webview_storage_dir,
)
from launcher import setup_application_logging


@pytest.fixture
def isolated_user_data(monkeypatch, tmp_path: Path) -> Path:
    """Isolate VoxPilot writable paths and SQLite engines to a temporary folder."""
    reset_db_connection()
    clear_executions()
    previous_agent = None
    try:
        from backend.api import agent as agent_api

        previous_agent = agent_api._SHARED_AGENT
    except Exception:
        pass

    user_root = tmp_path / "voxpilot_user_data"
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(user_root))
    for var in (
        "VOXPILOT_DB_PATH",
        "VOXPILOT_DB_URL",
        "VOXPILOT_CONFIG_DIR",
        "VOXPILOT_CREDENTIALS_DIR",
        "VOXPILOT_BACKUP_DIR",
        "VOXPILOT_SCREENSHOT_DIR",
        "VOXPILOT_DOWNLOAD_DIR",
        "VOXPILOT_WEBVIEW_DIR",
        "VOXPILOT_LOG_DIR",
        "VOXPILOT_EXPORT_DIR",
        "VOXPILOT_UPDATES_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    ensure_user_directories()
    yield user_root

    set_agent(previous_agent)
    clear_executions()
    reset_db_connection()


def test_frozen_and_dev_paths_keep_user_data_out_of_install_dir(monkeypatch, tmp_path: Path):
    """Verify frozen mode routes all writable paths under %LOCALAPPDATA%\\VoxPilot and never bundle/exe dirs."""
    bundle_dir = tmp_path / "ProgramFiles_VoxPilot" / "_internal"
    exe_dir = tmp_path / "ProgramFiles_VoxPilot"
    bundle_dir.mkdir(parents=True)
    fake_exe = exe_dir / "VoxPilot.exe"
    fake_exe.touch()
    fake_localappdata = tmp_path / "LocalAppData"

    for var in (
        "VOXPILOT_USER_DATA_DIR",
        "VOXPILOT_FORCE_USER_DATA",
        "VOXPILOT_DB_PATH",
        "VOXPILOT_CONFIG_DIR",
        "VOXPILOT_CREDENTIALS_DIR",
        "VOXPILOT_BACKUP_DIR",
        "VOXPILOT_SCREENSHOT_DIR",
        "VOXPILOT_DOWNLOAD_DIR",
        "VOXPILOT_WEBVIEW_DIR",
        "VOXPILOT_LOG_DIR",
        "VOXPILOT_EXPORT_DIR",
        "VOXPILOT_UPDATES_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

    ensure_user_directories()
    expected_root = (fake_localappdata / "VoxPilot").resolve()
    assert get_user_data_dir() == expected_root

    summary = get_runtime_paths_summary()
    assert summary["mode"] == "frozen"
    writable_keys = (
        "database_path",
        "config_dir",
        "credentials_dir",
        "backups_dir",
        "log_dir",
        "export_dir",
        "screenshots_dir",
        "downloads_dir",
        "voice_temp_dir",
        "webview_storage_dir",
        "updates_dir",
    )
    for key in writable_keys:
        resolved = Path(summary[key]).resolve()
        assert resolved.is_relative_to(expected_root), f"{key} ({resolved}) must be inside {expected_root}"
        assert not resolved.is_relative_to(exe_dir.resolve()), f"{key} must never live inside install dir"

    assert (expected_root / "credentials").is_dir()
    assert (expected_root / "backups").is_dir()
    assert (expected_root / "webview").is_dir()

    provider_store = WindowsDPAPIProviderCredentialStore()
    email_store = DPAPICredentialStore()
    assert provider_store.storage_dir.resolve() == get_credentials_dir()
    assert email_store.storage_dir.resolve() == (get_credentials_dir() / "email_profiles").resolve()


def test_legacy_malformed_plan_json_and_step_params_migration_and_api(isolated_user_data: Path):
    """Legacy malformed execution records must not crash conversation APIs and migrate cleanly."""
    init_db_sync()
    engine = get_sync_engine()
    repo = DatabaseRepository(sync_engine=engine)

    # Seed valid record and construct VoxPilotAgent BEFORE inserting raw legacy rows
    # so API endpoints are genuinely exercised against un-migrated malformed DB payloads first.
    repo.create_or_get_conversation_sync("conv_legacy_mix", title="Legacy Mix", profile_id="default")
    repo.save_execution_sync(
        {
            "execution_id": "exec_valid_1",
            "conversation_id": "conv_legacy_mix",
            "profile_id": "default",
            "user_input": "open notepad",
            "intent": "application_control",
            "execution_status": "completed",
            "plan": [{"action": "launch_application", "description": "Launch Notepad", "parameters": {"app": "notepad"}}],
            "response": "Opened Notepad.",
        }
    )

    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=ToolExecutor(registry=reg, confirmation_manager=cm),
        confirmation_manager=cm,
        repository=repo,
    )
    set_agent(agent)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO executionrecord "
                "(execution_id, conversation_id, profile_id, user_input, intent, execution_status, plan_json, current_step, response, error, created_at, updated_at) "
                "VALUES "
                "('exec_bad_list_str', 'conv_legacy_mix', 'default', 'legacy str list', 'open_application', 'validation_failed', '[\"just_a_string_not_a_dict\"]', 0, 'Validation failed', 'Malformed plan', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('exec_bad_scalar_json', 'conv_legacy_mix', 'default', 'legacy raw text', 'open_application', 'failed', 'not-valid-json-token sk-proj-secret1234567890', 0, 'Error', 'Corrupt json', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('exec_bad_dict', 'conv_legacy_mix', 'default', 'legacy single dict', 'system_information', 'completed', '{\"action\":\"get_system_info\",\"parameters\":\"raw_param\"}', 1, 'Done', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('exec_bad_mixed', 'conv_legacy_mix', 'default', 'legacy mixed array', 'general_conversation', 'completed', '[null, 42, \"string_step\", {\"action\":\"ok\"}]', 1, 'Done', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO executionsteprecord "
                "(step_id, execution_id, step_index, action, parameters_json, execution_status, started_at) "
                "VALUES ('step_bad_1', 'exec_valid_1', 0, 'legacy_action', '[\"legacy_array_param\"]', 'completed', CURRENT_TIMESTAMP)"
            )
        )

    client = TestClient(app)
    res_list = client.get("/agent/conversations/conv_legacy_mix/executions")
    assert res_list.status_code == 200
    items = res_list.json()
    assert len(items) == 5
    by_id = {item["execution_id"]: item for item in items}

    assert by_id["exec_valid_1"]["plan"][0]["action"] == "launch_application"
    assert by_id["exec_bad_list_str"]["plan"][0]["action"] == "legacy_step"
    assert by_id["exec_bad_list_str"]["plan"][0]["legacy_raw"] == "just_a_string_not_a_dict"
    assert "sk-proj-secret1234567890" not in json.dumps(by_id["exec_bad_scalar_json"]["plan"])
    assert by_id["exec_bad_dict"]["plan"][0]["parameters"] == {"_legacy_value": "raw_param"}
    assert len(by_id["exec_bad_mixed"]["plan"]) == 3

    res_status = client.get("/agent/status/exec_bad_list_str")
    assert res_status.status_code == 200
    assert res_status.json()["plan"][0]["action"] == "legacy_step"

    # Now run idempotent migration and confirm database rows are non-destructively repaired in-place
    with engine.begin() as conn:
        mig_result = run_migrations_with_connection(conn)
    assert "repaired_legacy_execution_plans:4" in mig_result["applied_steps"]
    assert "repaired_legacy_step_parameters:1" in mig_result["applied_steps"]

    # Second migration pass must be a clean no-op
    with engine.begin() as conn:
        mig_result_2 = run_migrations_with_connection(conn)
    assert not any(s.startswith("repaired_legacy_") for s in mig_result_2["applied_steps"])

    with engine.connect() as conn:
        row_count = conn.execute(
            text("SELECT COUNT(*) FROM executionrecord WHERE conversation_id = 'conv_legacy_mix'")
        ).scalar()
        assert row_count == 5
        repaired_json = conn.execute(
            text("SELECT plan_json FROM executionrecord WHERE execution_id = 'exec_bad_list_str'")
        ).scalar()
        parsed = json.loads(repaired_json)
        assert isinstance(parsed, list) and isinstance(parsed[0], dict)
        assert parsed[0]["legacy_raw"] == "just_a_string_not_a_dict"

        step_param_raw = conn.execute(
            text("SELECT parameters_json FROM executionsteprecord WHERE step_id = 'step_bad_1'")
        ).scalar()
        assert json.loads(step_param_raw) == {"_legacy_value": ["legacy_array_param"]}


def test_sqlite_wal_and_persistence_across_restarts(isolated_user_data: Path):
    """Database WAL pragmas and profile/provider/conversation/memory records persist across full engine resets."""
    init_db_sync()
    engine1 = get_sync_engine()
    with engine1.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode;")).scalar()
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert str(journal).lower() == "wal"
        assert int(fk) == 1

    repo1 = DatabaseRepository(sync_engine=engine1)
    repo1.save_execution_sync(
        {
            "execution_id": "exec_persist_1",
            "conversation_id": "conv_persist_1",
            "profile_id": "work",
            "user_input": "summarize status",
            "execution_status": "completed",
            "plan": [{"action": "get_system_info", "parameters": {}}],
            "response": "Healthy.",
        }
    )
    with engine1.begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO profilerecord "
                "(id, name, normalized_name, description, avatar, theme_mode, accent_color, is_active, is_default, created_at, updated_at) "
                "VALUES ('work', 'Work', 'work', 'Work profile', 'briefcase', 'light', 'emerald', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT OR REPLACE INTO profile_provider_configs "
                "(profile_id, active_provider_id, local_model, byok_vendor, byok_model, byok_credential_ref, config_json, updated_at) "
                "VALUES ('work', 'byok', 'qwen3:4b', 'openai', 'gpt-4o-mini', 'byok:work:openai', '{}', CURRENT_TIMESTAMP)"
            )
        )

    # Simulate application restart
    reset_db_connection()
    init_db_sync()
    engine2 = get_sync_engine()
    repo2 = DatabaseRepository(sync_engine=engine2)
    restored = repo2.get_execution_sync("exec_persist_1", profile_id="work")
    assert restored is not None
    assert restored.response == "Healthy."
    assert repo2.get_execution_sync("exec_persist_1", profile_id="default") is None

    with engine2.connect() as conn:
        prov_row = conn.execute(
            text("SELECT active_provider_id, byok_vendor, byok_credential_ref FROM profile_provider_configs WHERE profile_id = 'work'")
        ).fetchone()
        assert prov_row == ("byok", "openai", "byok:work:openai")


def test_database_backup_verify_non_overwrite_and_explicit_recovery(isolated_user_data: Path):
    """Online SQLite backup snapshots config + DPAPI files, blocks unsafe overwrite/auto-restore, and recovers cleanly."""
    init_db_sync()
    engine = get_sync_engine()
    repo = DatabaseRepository(sync_engine=engine)
    repo.save_execution_sync(
        {
            "execution_id": "exec_backup_1",
            "conversation_id": "conv_backup_1",
            "profile_id": "default",
            "user_input": "original task",
            "execution_status": "completed",
            "response": "Original result",
        }
    )

    cfg_path = get_config_dir() / "voice_settings.json"
    cfg_path.write_text(json.dumps({"shortcut": "Ctrl+Shift+M"}), encoding="utf-8")

    cred_file = get_credentials_dir() / "byok_default_openai.dpapi.json"
    cred_payload = {
        "version": 1,
        "profile_id": "default",
        "vendor": "openai",
        "credential_ref": "byok:default:openai",
        "dpapi_ciphertext": "FAKE_DPAPI_BASE64_CIPHERTEXT",
    }
    cred_file.write_text(json.dumps(cred_payload), encoding="utf-8")

    # Hold an active WAL reader/writer connection during backup creation
    live_wal_conn = sqlite3.connect(str(get_database_path()))
    try:
        live_wal_conn.execute("PRAGMA journal_mode=WAL;")
        backup_mgr = DatabaseBackupManager()
        manifest = backup_mgr.create_backup(label="stage_d_test")
    finally:
        live_wal_conn.close()

    backup_dir = Path(manifest["backup_dir"])
    assert backup_dir.is_dir()
    assert manifest["database"]["integrity_check"] == "ok"
    assert manifest["database"]["table_counts"]["executionrecord"] >= 1
    assert len(manifest["config_files"]) == 1
    assert len(manifest["credential_files"]) == 1
    assert "FAKE_DPAPI_BASE64_CIPHERTEXT" not in json.dumps(manifest)

    # Verify backup checksums & integrity
    verified = backup_mgr.verify_backup(backup_dir)
    assert verified["database"]["sha256"] == manifest["database"]["sha256"]

    # Overwriting non-empty explicit target directory must fail without allow_overwrite=True
    with pytest.raises(BackupAlreadyExistsError):
        backup_mgr.create_backup(target_dir=backup_dir, allow_overwrite=False)

    # Restore without confirm_overwrite=True must fail and leave current data untouched
    with pytest.raises(BackupRestoreConfirmationError):
        backup_mgr.restore_backup(backup_dir, confirm_overwrite=False)

    # Mutate live database, config, and delete credential, then recover explicitly
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM executionrecord WHERE execution_id = 'exec_backup_1'"))
    cfg_path.write_text(json.dumps({"shortcut": "Ctrl+Alt+Z"}), encoding="utf-8")
    cred_file.unlink()
    reset_db_connection()

    restore_result = backup_mgr.restore_backup(backup_dir, confirm_overwrite=True)
    assert restore_result["restored"] is True
    assert restore_result["pre_restore_backup_dir"] is not None
    assert Path(restore_result["pre_restore_backup_dir"]).is_dir()

    # Check restored state
    repo_after = DatabaseRepository(sync_engine=get_sync_engine())
    restored_exec = repo_after.get_execution_sync("exec_backup_1")
    assert restored_exec is not None
    assert restored_exec.response == "Original result"
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["shortcut"] == "Ctrl+Shift+M"
    assert json.loads(cred_file.read_text(encoding="utf-8"))["dpapi_ciphertext"] == "FAKE_DPAPI_BASE64_CIPHERTEXT"

    # Tampered backup must fail verification before restore
    tampered_cfg = backup_dir / "config" / "voice_settings.json"
    tampered_cfg.write_text(json.dumps({"shortcut": "TAMPERED"}), encoding="utf-8")
    with pytest.raises(BackupVerificationError):
        backup_mgr.verify_backup(backup_dir)


def test_dpapi_credential_profile_isolation_and_secret_log_redaction(isolated_user_data: Path):
    """Profile credential isolation rejects cross-profile resolution and logger scrubs API keys & desktop tokens."""
    fake_dpapi = MagicMock()
    fake_dpapi.protect_secret.side_effect = lambda s: f"ENC({s})"
    fake_dpapi.unprotect_secret.side_effect = lambda c: c[4:-1]

    store = WindowsDPAPIProviderCredentialStore(dpapi_backend=fake_dpapi)
    ref_work = store.store_credential("work", "openai", "sk-proj-worksecretvalue12345")
    assert ref_work == "byok:work:openai"
    # Atomic temp file must be cleaned up
    assert list(store.storage_dir.glob("*.tmp")) == []

    # Cross-profile resolution denied
    assert store.has_credential("personal", ref_work) is False
    assert store.delete_credential("personal", ref_work) is False
    with pytest.raises(ProviderConfigurationError):
        store.resolve_credential("personal", ref_work)
    assert store.resolve_credential("work", ref_work) == "sk-proj-worksecretvalue12345"

    # Sanitization & SecretRedactingLogFilter cover OpenAI, Anthropic, Gemini, Bearer & desktop token headers
    samples = [
        "OpenAI key sk-proj-supersecret12345 failed",
        "Anthropic key sk-ant-api03-secretvalue12345 failed",
        "Gemini key AIzaSyD1234567890abcdefgh failed",
        "Header Authorization: Bearer tok_secret_value_123456",
        "Desktop X-VoxPilot-Desktop-Token: dt_secret_token_98765",
    ]
    for sample in samples:
        redacted = sanitize_secret(sample)
        assert "secret" not in redacted.lower()
        assert "dt_secret_token" not in redacted

    vox_logger = logging.getLogger("voxpilot")
    saved_handlers = list(vox_logger.handlers)
    saved_filters = list(vox_logger.filters)
    vox_logger.handlers.clear()
    vox_logger.filters.clear()
    try:
        vox_logger = setup_application_logging()
        assert any(isinstance(f, SecretRedactingLogFilter) for f in vox_logger.filters)

        test_logger = logging.getLogger("voxpilot.stage_d_test")
        test_logger.setLevel(logging.INFO)
        log_path = get_log_dir() / "voxpilot.log"
        test_logger.info(
            "Rejecting request X-VoxPilot-Desktop-Token: my_secret_dt_token_xyz with key %s",
            "sk-ant-api03-mysecretkey999",
        )
        for h in vox_logger.handlers:
            h.flush()
        log_contents = log_path.read_text(encoding="utf-8")
        assert "my_secret_dt_token_xyz" not in log_contents
        assert "sk-ant-api03-mysecretkey999" not in log_contents
    finally:
        for h in list(vox_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
        vox_logger.handlers[:] = saved_handlers
        vox_logger.filters[:] = saved_filters


def test_storage_and_backups_api_enforces_desktop_token_and_origin(isolated_user_data: Path):
    """Stage D storage & backup endpoints enforce desktop token + Origin checks and return sanitized metadata."""
    init_db_sync()
    previous = get_shared_desktop_controller()
    controller = DesktopShellController(host="127.0.0.1", port=51234, require_session_token=True)
    set_shared_desktop_controller(controller)
    client = TestClient(app)

    try:
        # Missing token -> 401
        assert client.get("/api/system/storage").status_code == 401
        assert client.post("/api/system/backups", json={"label": "unauth"}).status_code == 401

        # External origin even with token -> 403
        bad_origin_headers = {
            "X-VoxPilot-Desktop-Token": controller.session_token,
            "Origin": "https://evil.example.com",
        }
        assert client.get("/api/system/storage", headers=bad_origin_headers).status_code == 403

        # Trusted desktop token + loopback origin -> 200
        ok_headers = {
            "X-VoxPilot-Desktop-Token": controller.session_token,
            "Origin": "http://127.0.0.1:51234",
        }
        storage_res = client.get("/api/system/storage", headers=ok_headers)
        assert storage_res.status_code == 200
        storage_data = storage_res.json()
        assert Path(storage_data["paths"]["credentials_dir"]).resolve() == get_credentials_dir()
        assert storage_data["database"]["exists"] is True

        bkp_res = client.post(
            "/api/system/backups",
            json={"label": "api_snapshot"},
            headers=ok_headers,
        )
        assert bkp_res.status_code == 200
        manifest = bkp_res.json()
        assert manifest["database"]["integrity_check"] == "ok"
        assert Path(manifest["backup_dir"]).is_relative_to(get_backups_dir())
    finally:
        set_shared_desktop_controller(previous)
