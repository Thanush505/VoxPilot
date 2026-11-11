"""
Unit and integration tests for VoxPilot Audit Trail Export Subsystem.
Tests SQLite audit record querying, sensitive token/credential scrubbing,
JSON and CSV export generation, destination containment, overwrite confirmation,
REST API endpoints, and LLM isolation.
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database.connection import init_db_sync, get_sync_engine
from backend.database.models import ExecutionRecord, ExecutionStepRecord, ConversationRecord
from backend.database.audit_export import (
    AuditExporter,
    scrub_sensitive_data,
    get_shared_audit_exporter,
    set_shared_audit_exporter,
)
from backend.agent.tool_registry import get_shared_tool_registry
from sqlmodel import Session


@pytest.fixture(autouse=True)
def setup_test_audit_db(tmp_path: Path):
    """Create a temporary SQLite database populated with audit records."""
    db_file = tmp_path / "test_audit.db"
    db_url = f"sqlite:///{db_file}"
    init_db_sync(db_url)
    engine = get_sync_engine(db_url)

    export_dir = tmp_path / "exports"
    exporter = AuditExporter(export_dir=export_dir, engine=engine)
    set_shared_audit_exporter(exporter)

    # Seed test conversation, executions, and steps
    with Session(engine) as session:
        conv = ConversationRecord(
            conversation_id="conv_audit_100",
            title="Audit Test Conversation",
        )
        session.add(conv)

        # Execution 1: completed
        now = datetime.now(timezone.utc)
        exec1 = ExecutionRecord(
            execution_id="exec_audit_001",
            conversation_id="conv_audit_100",
            user_input="type hello in notepad",
            intent="keyboard_control",
            execution_status="completed",
            plan_json=json.dumps([{"action": "type_text", "parameters": {"text": "hello", "token": "secret_tok_123"}}]),
            response="Successfully typed text.",
            created_at=now,
            completed_at=now,
        )
        session.add(exec1)

        step1 = ExecutionStepRecord(
            step_id="exec_audit_001_s0",
            execution_id="exec_audit_001",
            step_index=0,
            action="type_text",
            parameters_json=json.dumps({"text": "hello", "confirmation_token": "secret_tok_123"}),
            execution_status="completed",
            result_summary="Typed text successfully",
            started_at=now,
            completed_at=now,
        )
        session.add(step1)

        # Execution 2: failed
        exec2 = ExecutionRecord(
            execution_id="exec_audit_002",
            conversation_id="conv_audit_100",
            user_input="open malicious url",
            intent="browser_control",
            execution_status="failed",
            plan_json=json.dumps([{"action": "navigate_url", "parameters": {"url": "http://127.0.0.1"}}]),
            response="Navigation blocked for SSRF protection.",
            error="Blocked IP address",
            created_at=now,
            completed_at=now,
        )
        session.add(exec2)

        session.commit()

    yield exporter


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSensitiveDataScrubbing:
    """Verifies that sensitive credentials, tokens, and keys are thoroughly scrubbed."""

    def test_scrub_dict_keys(self):
        raw = {
            "action": "type_text",
            "token": "sensitive_jwt_token_12345",
            "confirmation_token": "conf_tok_abcdef",
            "password": "SuperSecretPassword123!",
            "api_key": "vox_live_key_999",
            "nested": {
                "auth_header": "Bearer secret_header",
                "safe_field": "public_data",
            },
        }

        scrubbed = scrub_sensitive_data(raw)
        assert scrubbed["token"] == "[REDACTED]"
        assert scrubbed["confirmation_token"] == "[REDACTED]"
        assert scrubbed["password"] == "[REDACTED]"
        assert scrubbed["api_key"] == "[REDACTED]"
        assert scrubbed["nested"]["auth_header"] == "[REDACTED]"
        assert scrubbed["nested"]["safe_field"] == "public_data"


class TestAuditExporterFilesystem:
    """Tests file export generation and safety controls."""

    def test_export_json_success(self, setup_test_audit_db: AuditExporter):
        exporter = setup_test_audit_db
        res = exporter.export_json(filename="audit_log.json")

        assert res["format"] == "json"
        assert res["record_count"] == 2
        assert Path(res["file_path"]).exists()

        content = json.loads(Path(res["file_path"]).read_text(encoding="utf-8"))
        assert content["record_count"] == 2
        assert len(content["records"]) == 2

        # Verify token scrubbing in JSON content
        for rec in content["records"]:
            if rec["execution_id"] == "exec_audit_001":
                assert rec["steps"][0]["parameters"]["confirmation_token"] == "[REDACTED]"

    def test_export_csv_success(self, setup_test_audit_db: AuditExporter):
        exporter = setup_test_audit_db
        res = exporter.export_csv(filename="audit_log.csv")

        assert res["format"] == "csv"
        assert res["record_count"] == 2
        assert Path(res["file_path"]).exists()

        with open(res["file_path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) >= 2
        exec_ids = {r["execution_id"] for r in rows}
        assert "exec_audit_001" in exec_ids
        assert "exec_audit_002" in exec_ids

    def test_path_traversal_prevention(self, setup_test_audit_db: AuditExporter):
        exporter = setup_test_audit_db
        traversal_attempts = [
            "../secret.json",
            "..\\windows\\system32.json",
            "subfolder/audit.json",
            "/etc/passwd.json",
        ]

        for bad_filename in traversal_attempts:
            with pytest.raises(ValueError) as exc_info:
                exporter.export_json(filename=bad_filename)
            assert "traversal" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()

    def test_overwrite_protection(self, setup_test_audit_db: AuditExporter):
        exporter = setup_test_audit_db
        filename = "test_overwrite.json"
        # First export succeeds
        exporter.export_json(filename=filename)

        # Second export with confirm_overwrite=False must raise FileExistsError
        with pytest.raises(FileExistsError) as exc_info:
            exporter.export_json(filename=filename, confirm_overwrite=False)
        assert "already exists" in str(exc_info.value)

        # Overwrite with confirm_overwrite=True succeeds
        res_overwrite = exporter.export_json(filename=filename, confirm_overwrite=True)
        assert res_overwrite["filename"] == filename

    def test_filters(self, setup_test_audit_db: AuditExporter):
        exporter = setup_test_audit_db
        # Filter by status: failed only
        failed_records = exporter.query_audit_records(filters={"status": "failed"})
        assert len(failed_records) == 1
        assert failed_records[0]["execution_id"] == "exec_audit_002"

        # Filter by execution_id
        exact_record = exporter.query_audit_records(filters={"execution_id": "exec_audit_001"})
        assert len(exact_record) == 1
        assert exact_record[0]["execution_id"] == "exec_audit_001"


class TestAuditExportApiEndpoints:
    """Tests the REST API endpoints for audit export."""

    def test_api_export_json_and_status(self, client: TestClient):
        payload = {
            "format": "json",
            "filename": "api_audit.json",
            "confirm_overwrite": True,
        }
        res = client.post("/agent/audit/export", json=payload, headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 200
        data = res.json()
        export_id = data["export_id"]
        assert data["format"] == "json"
        assert data["filename"] == "api_audit.json"
        assert data["record_count"] >= 1

        # Check status endpoint
        status_res = client.get(f"/agent/audit/exports/{export_id}/status", headers={"Origin": "http://localhost:3000"})
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["export_id"] == export_id

    def test_api_export_csv(self, client: TestClient):
        payload = {
            "format": "csv",
            "filename": "api_audit.csv",
            "confirm_overwrite": True,
        }
        res = client.post("/agent/audit/export", json=payload, headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 200
        assert res.json()["format"] == "csv"

    def test_api_csrf_protection(self, client: TestClient):
        payload = {"format": "json"}
        res = client.post("/agent/audit/export", json=payload, headers={"Origin": "http://evil.com"})
        assert res.status_code == 403
        assert "forbidden" in res.json()["detail"].lower()

    def test_api_list_and_download_export(self, client: TestClient):
        # Generate an export
        payload = {
            "format": "json",
            "filename": "download_test.json",
            "confirm_overwrite": True,
        }
        create_res = client.post("/agent/audit/export", json=payload, headers={"Origin": "http://localhost:3000"})
        assert create_res.status_code == 200
        data = create_res.json()
        export_id = data["export_id"]

        # List exports
        list_res = client.get("/agent/audit/exports", headers={"Origin": "http://localhost:3000"})
        assert list_res.status_code == 200
        exports = list_res.json()
        assert any(e["export_id"] == export_id for e in exports)

        # Download export
        dl_res = client.get(f"/agent/audit/exports/{export_id}/download", headers={"Origin": "http://localhost:3000"})
        assert dl_res.status_code == 200
        assert dl_res.headers.get("content-type", "").startswith("application/json")
        assert "attachment" in dl_res.headers.get("content-disposition", "")
        body = dl_res.json()
        assert body["export_id"] == export_id
        assert "records" in body

    def test_api_download_traversal_rejection(self, client: TestClient):
        bad_ids = ["../secret", "..\\windows", "sub/id"]
        for bad_id in bad_ids:
            res = client.get(f"/agent/audit/exports/{bad_id}/download", headers={"Origin": "http://localhost:3000"})
            assert res.status_code in {400, 404}

    def test_api_download_csrf_protection(self, client: TestClient):
        res = client.get("/agent/audit/exports/exp_12345/download", headers={"Origin": "http://malicious-site.com"})
        assert res.status_code == 403

    def test_api_download_not_found(self, client: TestClient):
        res = client.get("/agent/audit/exports/exp_nonexistent/download", headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 404

    def test_audit_export_strictly_omitted_from_llm_tools(self):
        """Verify that audit export, reset, and recovery endpoints are NOT exposed to the LLM."""
        registry = get_shared_tool_registry()
        tool_names = set(registry.list_tools())
        assert "export_audit_trail" not in tool_names
        assert "export_json" not in tool_names
        assert "export_csv" not in tool_names
        assert "reset_emergency_stop" not in tool_names
        assert "operator_emergency_recovery" not in tool_names

