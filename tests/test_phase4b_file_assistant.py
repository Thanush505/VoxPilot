"""
Tests for Phase 4B: AI File Assistant.

Covers:
- DocumentExtractor (TXT, MD, PY, JSON, CSV, DOCX, PDF fallback/encrypted/corrupted/unavailable)
- Empty, unsupported, and oversized/truncated document handling
- FileAssistantService search, recent-file filtering, read-only project search, and path traversal safety
- Document-grounded summarization, Q&A, and multi-document comparison (mocked Ollama + offline fallback)
- Folder organization confirmation requirement
- FileManager.create_file(content=...) and overwrite confirmation guard
- IntentRecognizer, AgentPlanner, and VoxPilotAgent integration (including memory isolation & EmergencyStop)
"""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.ai.file_assistant import (
    DocumentExtractor,
    FileAssistantService,
)
from backend.agent.graph import VoxPilotAgent
from backend.agent.intent import IntentRecognizer
from backend.agent.planner import AgentPlanner
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.automation.file_manager import ConfirmationRequiredError, FileManager
from backend.database.connection import init_db_sync, reset_db_connection
from backend.tools.files import CreateFileInput, create_file_tool


def _make_minimal_docx(path: Path, body_xml: str) -> None:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", document_xml)


def _make_simple_pdf(path: Path, text: str) -> None:
    stream_body = f"BT\n/F1 12 Tf\n72 720 Td\n({text}) Tj\nET\n".encode("latin-1")
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(stream_body)} >>\nstream\n".encode("latin-1")
        + stream_body
        + b"endstream\nendobj\n%%EOF\n"
    )
    path.write_bytes(pdf_bytes)


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


class TestDocumentExtractor:
    def test_extracts_plain_text_md_py_json_and_csv(self, tmp_path: Path):
        extractor = DocumentExtractor()

        txt = tmp_path / "notes.txt"
        txt.write_text("Project alpha kickoff notes.", encoding="utf-8")
        res_txt = extractor.extract(txt)
        assert res_txt.success is True
        assert res_txt.status == "success"
        assert "Project alpha kickoff notes." in res_txt.text

        md = tmp_path / "readme.md"
        md.write_text("# Heading\n- Bullet one", encoding="utf-8")
        res_md = extractor.extract(md)
        assert res_md.success is True
        assert "# Heading" in res_md.text

        py_file = tmp_path / "app.py"
        py_file.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        res_py = extractor.extract(py_file)
        assert res_py.success is True
        assert "def greet():" in res_py.text

        js = tmp_path / "config.json"
        js.write_text('{"mode":"local","model":"qwen3:4b"}', encoding="utf-8")
        res_js = extractor.extract(js)
        assert res_js.success is True
        assert '"model": "qwen3:4b"' in res_js.text

        csv_f = tmp_path / "budget.csv"
        csv_f.write_text("Item,Cost\nServer,120\nDomain,15\n", encoding="utf-8")
        res_csv = extractor.extract(csv_f)
        assert res_csv.success is True
        assert "| Item | Cost |" in res_csv.text
        assert "| Server | 120 |" in res_csv.text

    def test_extracts_docx_headings_paragraphs_and_tables(self, tmp_path: Path):
        docx_path = tmp_path / "spec.docx"
        body_xml = (
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Requirements</w:t></w:r></w:p>'
            "<w:p><w:r><w:t>Local-first execution is mandatory.</w:t></w:r></w:p>"
            "<w:tbl><w:tr>"
            "<w:tc><w:p><w:r><w:t>Component</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc>"
            "</w:tr><w:tr>"
            "<w:tc><w:p><w:r><w:t>FileAssistant</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Ready</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:tbl>"
        )
        _make_minimal_docx(docx_path, body_xml)

        res = DocumentExtractor().extract(docx_path)
        assert res.success is True
        assert res.extractor == "docx_xml"
        assert "# Requirements" in res.text
        assert "Local-first execution is mandatory." in res.text
        assert "| Component | Status |" in res.text
        assert "| FileAssistant | Ready |" in res.text

    def test_extracts_simple_pdf_and_detects_encrypted_corrupted_unavailable(self, tmp_path: Path):
        extractor = DocumentExtractor()

        simple_pdf = tmp_path / "simple.pdf"
        _make_simple_pdf(simple_pdf, "VoxPilot Phase 4B PDF Text")
        res_simple = extractor.extract(simple_pdf)
        assert res_simple.success is True
        assert "VoxPilot Phase 4B PDF Text" in res_simple.text

        enc_pdf = tmp_path / "secret.pdf"
        enc_pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Encrypt 2 0 R >> endobj\n%%EOF")
        res_enc = extractor.extract(enc_pdf)
        assert res_enc.success is False
        assert res_enc.status == "encrypted"

        bad_pdf = tmp_path / "broken.pdf"
        bad_pdf.write_bytes(b"NOT A PDF")
        res_bad = extractor.extract(bad_pdf)
        assert res_bad.success is False
        assert res_bad.status == "corrupted"

        # Valid PDF header with no extractable text (simulates scanned PDF without pypdf)
        scanned_pdf = tmp_path / "scanned.pdf"
        scanned_pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF")
        with patch.object(DocumentExtractor, "_try_optional_pdf_libs", return_value=(None, "none")):
            res_scan = extractor.extract(scanned_pdf)
        assert res_scan.success is False
        assert res_scan.status == "extraction_unavailable"

    def test_empty_unsupported_corrupted_docx_and_truncation(self, tmp_path: Path):
        extractor = DocumentExtractor()

        empty_f = tmp_path / "empty.txt"
        empty_f.write_text("", encoding="utf-8")
        res_empty = extractor.extract(empty_f)
        assert res_empty.success is True
        assert res_empty.is_empty is True
        assert res_empty.status == "empty"

        bin_f = tmp_path / "image.png"
        bin_f.write_bytes(b"\x89PNG\r\n\x1a\n")
        res_bin = extractor.extract(bin_f)
        assert res_bin.success is False
        assert res_bin.status == "unsupported_format"

        bad_docx = tmp_path / "corrupt.docx"
        bad_docx.write_bytes(b"not a zip archive")
        res_bad_docx = extractor.extract(bad_docx)
        assert res_bad_docx.success is False
        assert res_bad_docx.status == "corrupted"

        long_f = tmp_path / "large.txt"
        long_f.write_text("A" * 500, encoding="utf-8")
        res_trunc = extractor.extract(long_f, max_chars=120)
        assert res_trunc.success is True
        assert res_trunc.was_truncated is True
        assert res_trunc.total_chars == 500
        assert res_trunc.included_chars == 120
        assert "truncated" in res_trunc.message.lower()


class TestFileAssistantService:
    def test_find_files_recency_and_path_traversal_rejection(self, tmp_path: Path):
        approved = tmp_path / "Downloads"
        approved.mkdir()
        outside = tmp_path / "OutsideSecret"
        outside.mkdir()
        secret_file = outside / "secret.txt"
        secret_file.write_text("do not read", encoding="utf-8")

        recent_file = approved / "resume_2026.txt"
        recent_file.write_text("My resume", encoding="utf-8")
        old_file = approved / "old_notes.txt"
        old_file.write_text("Old", encoding="utf-8")
        old_ts = time.time() - (20 * 86400)
        os.utime(old_file, (old_ts, old_ts))

        fm = FileManager(approved_directories=[approved], project_root=tmp_path / "Proj")
        svc = FileAssistantService(file_manager=fm, project_root=tmp_path / "Proj")

        # Find recent files only
        recent_res = svc.find_files(directory_path=approved, modified_within_days=7, sort_by="modified")
        assert recent_res["success"] is True
        names = [i["name"] for i in recent_res["items"]]
        assert "resume_2026.txt" in names
        assert "old_notes.txt" not in names

        # Reject path outside approved directories
        read_outside = svc.read_document(secret_file)
        assert read_outside.success is False
        assert read_outside.status == "not_allowed"

    def test_readonly_project_python_search_blocks_sensitive_dirs(self, tmp_path: Path):
        proj = tmp_path / "VoxPilot"
        (proj / "backend").mkdir(parents=True)
        (proj / "backend" / "main.py").write_text("print('ok')", encoding="utf-8")
        (proj / ".venv").mkdir()
        (proj / ".venv" / "site.py").write_text("hidden", encoding="utf-8")

        fm = FileManager(approved_directories=[tmp_path / "Docs"], project_root=proj)
        svc = FileAssistantService(file_manager=fm, project_root=proj)

        res = svc.find_files(extension=".py", include_project_readonly=True)
        assert res["success"] is True
        paths = [i["path"] for i in res["items"]]
        assert any(p.endswith("main.py") for p in paths)
        assert not any(".venv" in p for p in paths)

    def test_summarize_qa_compare_and_ollama_offline(self, tmp_path: Path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        doc1 = docs_dir / "requirements.txt"
        doc1.write_text("The system must run offline on 16GB RAM.", encoding="utf-8")
        doc2 = docs_dir / "requirements_v2.txt"
        doc2.write_text("The system must run offline on 16GB RAM and support DOCX.", encoding="utf-8")

        fm = FileManager(approved_directories=[docs_dir], project_root=tmp_path / "Proj")
        svc = FileAssistantService(file_manager=fm, project_root=tmp_path / "Proj")

        fake_resp = SimpleNamespace(message=SimpleNamespace(content="Summary: Runs offline on 16GB RAM."))
        with patch("backend.ai.file_assistant.ollama.chat", return_value=fake_resp):
            sum_res = svc.summarize_document(doc1)
            assert sum_res["success"] is True
            assert "16GB RAM" in sum_res["message"]

            qa_res = svc.answer_document_question(doc1, "What is the RAM requirement?")
            assert qa_res["success"] is True

            cmp_res = svc.compare_documents([doc1, doc2])
            assert cmp_res["success"] is True

        # Ollama unavailable error handling
        with patch("backend.ai.file_assistant.ollama.chat", side_effect=ConnectionError("Connection refused")):
            offline_res = svc.summarize_document(doc1)
            assert offline_res["success"] is False
            assert offline_res["status"] == "llm_unavailable"
            assert "unavailable" in offline_res["message"].lower()

    def test_plan_folder_organization_requires_confirmation(self, tmp_path: Path):
        dl = tmp_path / "Downloads"
        dl.mkdir()
        (dl / "photo.jpg").write_bytes(b"jpg")
        (dl / "report.pdf").write_bytes(b"%PDF-1.4")

        fm = FileManager(approved_directories=[dl], project_root=tmp_path / "Proj")
        svc = FileAssistantService(file_manager=fm, project_root=tmp_path / "Proj")

        plan = svc.plan_folder_organization(dl)
        assert plan["success"] is True
        assert plan["requires_confirmation"] is True
        assert plan["status"] == "confirmation_required"
        assert plan["file_count"] == 2
        # Files must NOT be moved yet
        assert (dl / "photo.jpg").exists()
        assert (dl / "report.pdf").exists()

    def test_emergency_stop_blocks_file_assistant(self, tmp_path: Path):
        docs = tmp_path / "Documents"
        docs.mkdir()
        fm = FileManager(approved_directories=[docs], project_root=tmp_path / "Proj")
        svc = FileAssistantService(file_manager=fm, project_root=tmp_path / "Proj")

        get_shared_emergency_stop_manager().trigger_emergency_stop("test halt")
        res = svc.find_files(directory_path=docs)
        assert res["success"] is False
        assert res["status"] == "cancelled"


class TestFileManagerContentAndTools:
    def test_create_file_with_content_and_overwrite_confirmation(self, tmp_path: Path):
        docs = tmp_path / "Documents"
        docs.mkdir()
        fm = FileManager(approved_directories=[docs], project_root=tmp_path / "Proj")

        target = docs / "my_notes.txt"
        res = create_file_tool(
            CreateFileInput(file_path=str(target), content="Line 1\nLine 2"),
            manager=fm,
        )
        assert res.success is True
        assert target.read_text(encoding="utf-8") == "Line 1\nLine 2"

        # Second creation without confirm_overwrite must require confirmation
        res_conf = create_file_tool(
            CreateFileInput(file_path=str(target), content="Overwrite"),
            manager=fm,
        )
        assert res_conf.success is False
        assert res_conf.requires_confirmation is True
        assert res_conf.status == "confirmation_required"
        assert target.read_text(encoding="utf-8") == "Line 1\nLine 2"


class TestAgentRoutingAndMemoryIsolation:
    def test_intent_and_planner_recognize_file_assistant_commands(self):
        recognizer = IntentRecognizer()
        for prompt in [
            "Find my resume",
            "Summarize this PDF",
            "What does this document say about project requirements?",
            "Find all Python files inside my VoxPilot project",
            "Compare these two documents",
            "Find files modified recently in Downloads",
            "Create a text file containing these notes: Buy milk",
            "Organize these files into folders",
        ]:
            res = recognizer.recognize_intent(prompt)
            assert res["intent"] == "file_management", f"Failed intent for: {prompt}"

        planner = AgentPlanner()
        plan = planner.create_plan(
            user_input="Create a text file containing these notes: Buy milk and eggs",
            intent="file_management",
        )
        assert plan["steps"][0]["action"] == "create_file"
        assert "Buy milk and eggs" in plan["steps"][0]["parameters"]["content"]

    def test_agent_file_qa_skips_long_term_memory_storage(self, tmp_path: Path):
        reset_db_connection()
        init_db_sync(f"sqlite:///{tmp_path / 'test_4b.db'}")
        docs = tmp_path / "Documents"
        docs.mkdir()
        secret_doc = docs / "confidential.txt"
        secret_doc.write_text("Confidential salary budget is 999999.", encoding="utf-8")

        fm = FileManager(approved_directories=[docs], project_root=tmp_path / "Proj")
        svc = FileAssistantService(file_manager=fm, project_root=tmp_path / "Proj")
        agent = VoxPilotAgent()

        fake_resp = SimpleNamespace(message=SimpleNamespace(content="The document states the budget is 999999."))
        with (
            patch("backend.agent.graph.get_shared_file_assistant", return_value=svc),
            patch("backend.ai.file_assistant.ollama.chat", return_value=fake_resp),
            patch.object(agent.memory_extractor, "extract_and_store") as mock_extract,
        ):
            state = agent.process_request(
                user_input=f"Summarize '{secret_doc}'",
                conversation_id="conv_4b_isolation",
                profile_id="work_profile",
            )

        assert state["execution_status"] == "completed"
        assert "999999" in state["response"]
        mock_extract.assert_not_called()
