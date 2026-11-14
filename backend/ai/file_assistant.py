"""
AI File Assistant Service for VoxPilot (Phase 4B).

Provides safe, local-first document text extraction, file discovery,
document-grounded Q&A, summarization, and multi-document comparison
using local Ollama (`qwen3:4b`).

Design & Safety Guarantees:
- Reuses FileManager path validation and approved-directory containment.
- Read-only project source search is strictly isolated from write/delete paths.
- Never logs or persists full document contents into long-term memory.
- Enforces file size and character limits with explicit truncation notices.
- Respects EmergencyStopManager and ModelManager inference tracking.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import ollama

from backend.ai.model_manager import get_shared_model_manager
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.automation.file_manager import (
    FileInfo,
    FileManager,
    FileManagerError,
    PathNotAllowedError,
    PathNotFoundError,
    PathTraversalError,
    get_default_approved_directories,
    get_default_project_root,
)

logger = logging.getLogger(__name__)

# Size and context limits (safe for 16GB RAM / 6GB VRAM local inference)
MAX_READ_FILE_BYTES = 10 * 1024 * 1024  # 10 MB max read cap
MAX_DOCX_BYTES = 20 * 1024 * 1024       # 20 MB max DOCX archive size
MAX_PDF_BYTES = 25 * 1024 * 1024        # 25 MB max PDF size
DEFAULT_MAX_CHARS = 12000               # Per-document LLM context cap
DEFAULT_COMPARE_MAX_CHARS = 8000        # Per-document cap when comparing 2+ docs

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".json", ".csv", ".log",
    ".ini", ".cfg", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".ps1",
    ".toml", ".env", ".rst",
}

SUPPORTED_DOC_EXTENSIONS = TEXT_EXTENSIONS | {".docx", ".pdf"}

DEFAULT_ORGANIZATION_CATEGORIES: Dict[str, set[str]] = {
    "Documents": {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".rtf", ".odt"},
    "Spreadsheets": {".csv", ".xlsx", ".xls", ".ods"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"},
    "Code": {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".html", ".css", ".sql", ".sh", ".ps1"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Media": {".mp3", ".wav", ".mp4", ".mkv", ".mov", ".avi"},
}


@dataclass
class ExtractedDocument:
    """Result of extracting structured text from a local file."""
    path: str
    name: str
    extension: str
    success: bool
    status: str  # "success", "empty", "encrypted", "corrupted", "unsupported_format", "extraction_unavailable", "too_large", "not_found", "not_allowed", "error"
    text: str = ""
    message: str = ""
    size_bytes: int = 0
    total_chars: int = 0
    included_chars: int = 0
    was_truncated: bool = False
    is_empty: bool = False
    extractor: str = "none"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "extension": self.extension,
            "success": self.success,
            "status": self.status,
            "size_bytes": self.size_bytes,
            "total_chars": self.total_chars,
            "included_chars": self.included_chars,
            "was_truncated": self.was_truncated,
            "is_empty": self.is_empty,
            "extractor": self.extractor,
            "message": self.message,
            "metadata": self.metadata,
        }


class DocumentExtractor:
    """Extracts text from local documents using stdlib and optional PDF libraries."""

    def extract(
        self,
        file_path: Union[str, Path],
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> ExtractedDocument:
        """Extract text from a validated file path with size and error guards."""
        path = Path(file_path)
        ext = path.suffix.lower()
        name = path.name

        if not path.exists():
            return ExtractedDocument(
                path=str(path), name=name, extension=ext,
                success=False, status="not_found",
                message=f"File not found: '{path}'.",
            )
        if path.is_dir():
            return ExtractedDocument(
                path=str(path), name=name, extension=ext,
                success=False, status="unsupported_format",
                message=f"'{path}' is a directory, not a readable document file.",
            )

        try:
            size_bytes = path.stat().st_size
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=name, extension=ext,
                success=False, status="error",
                message=f"Could not read file metadata for '{name}': {err}",
            )

        if size_bytes == 0:
            return ExtractedDocument(
                path=str(path), name=name, extension=ext,
                success=True, status="empty", text="",
                size_bytes=0, total_chars=0, included_chars=0,
                is_empty=True, extractor="empty",
                message=f"File '{name}' is empty (0 bytes).",
            )

        if ext not in SUPPORTED_DOC_EXTENSIONS:
            return ExtractedDocument(
                path=str(path), name=name, extension=ext,
                success=False, status="unsupported_format",
                size_bytes=size_bytes,
                message=(
                    f"Unsupported file format '{ext or 'unknown'}' for '{name}'. "
                    "Supported formats include TXT, MD, CSV, JSON, PY, DOCX, and text-based PDFs."
                ),
            )

        if ext == ".docx":
            return self._extract_docx(path, size_bytes, max_chars)
        if ext == ".pdf":
            return self._extract_pdf(path, size_bytes, max_chars)
        if ext == ".csv":
            return self._extract_csv(path, size_bytes, max_chars)
        if ext == ".json":
            return self._extract_json(path, size_bytes, max_chars)
        return self._extract_plain_text(path, size_bytes, max_chars)

    def _finalize_text(
        self,
        path: Path,
        size_bytes: int,
        raw_text: str,
        max_chars: int,
        extractor: str,
        file_truncated: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExtractedDocument:
        cleaned = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=path.suffix.lower(),
                success=True, status="empty", text="",
                size_bytes=size_bytes, total_chars=0, included_chars=0,
                is_empty=True, extractor=extractor,
                message=f"File '{path.name}' contains no readable text.",
                metadata=metadata or {},
            )

        total_chars = len(cleaned)
        was_truncated = file_truncated or (total_chars > max_chars)
        included = cleaned[:max_chars] if total_chars > max_chars else cleaned

        msg = f"Extracted {len(included)} characters from '{path.name}'."
        if was_truncated:
            msg = (
                f"Extracted {len(included)} of {total_chars} characters from '{path.name}' "
                f"(content truncated to fit local context limit)."
            )

        logger.info(
            "Document extracted: name=%s ext=%s bytes=%d chars=%d truncated=%s extractor=%s",
            path.name, path.suffix.lower(), size_bytes, len(included), was_truncated, extractor,
        )
        return ExtractedDocument(
            path=str(path),
            name=path.name,
            extension=path.suffix.lower(),
            success=True,
            status="success",
            text=included,
            message=msg,
            size_bytes=size_bytes,
            total_chars=total_chars,
            included_chars=len(included),
            was_truncated=was_truncated,
            is_empty=False,
            extractor=extractor,
            metadata=metadata or {},
        )

    def _decode_bytes(self, raw: bytes) -> Optional[str]:
        if b"\x00" in raw[:4096]:
            # Check for UTF-16 BOM before rejecting binary data
            if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                try:
                    return raw.decode("utf-16")
                except UnicodeDecodeError:
                    return None
            return None

        for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    def _read_bounded_bytes(self, path: Path, limit: int) -> Tuple[bytes, bool]:
        file_truncated = False
        with open(path, "rb") as f:
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raw = raw[:limit]
            file_truncated = True
        return raw, file_truncated

    def _extract_plain_text(self, path: Path, size_bytes: int, max_chars: int) -> ExtractedDocument:
        try:
            raw, file_truncated = self._read_bounded_bytes(path, MAX_READ_FILE_BYTES)
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=path.suffix.lower(),
                success=False, status="error", size_bytes=size_bytes,
                message=f"Could not read '{path.name}': {err}",
            )

        decoded = self._decode_bytes(raw)
        if decoded is None:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=path.suffix.lower(),
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"File '{path.name}' appears to be binary or corrupted and cannot be read as text.",
            )

        return self._finalize_text(
            path, size_bytes, decoded, max_chars,
            extractor="plain_text", file_truncated=file_truncated,
        )

    def _extract_csv(self, path: Path, size_bytes: int, max_chars: int) -> ExtractedDocument:
        try:
            raw, file_truncated = self._read_bounded_bytes(path, MAX_READ_FILE_BYTES)
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".csv",
                success=False, status="error", size_bytes=size_bytes,
                message=f"Could not read CSV '{path.name}': {err}",
            )

        decoded = self._decode_bytes(raw)
        if decoded is None:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".csv",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"CSV file '{path.name}' contains unreadable binary bytes.",
            )

        try:
            reader = csv.reader(io.StringIO(decoded))
            rows = list(reader)
            if not rows:
                return self._finalize_text(path, size_bytes, "", max_chars, extractor="csv")

            lines: List[str] = []
            headers = rows[0]
            lines.append("| " + " | ".join(h.strip() for h in headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in rows[1:]:
                padded = row + [""] * max(0, len(headers) - len(row))
                lines.append("| " + " | ".join(cell.strip() for cell in padded[:len(headers)]) + " |")

            formatted = "\n".join(lines)
            return self._finalize_text(
                path, size_bytes, formatted, max_chars,
                extractor="csv_table", file_truncated=file_truncated,
                metadata={"row_count": len(rows), "column_count": len(headers)},
            )
        except csv.Error:
            return self._finalize_text(
                path, size_bytes, decoded, max_chars,
                extractor="csv_raw_fallback", file_truncated=file_truncated,
            )

    def _extract_json(self, path: Path, size_bytes: int, max_chars: int) -> ExtractedDocument:
        try:
            raw, file_truncated = self._read_bounded_bytes(path, MAX_READ_FILE_BYTES)
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".json",
                success=False, status="error", size_bytes=size_bytes,
                message=f"Could not read JSON '{path.name}': {err}",
            )

        decoded = self._decode_bytes(raw)
        if decoded is None:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".json",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"JSON file '{path.name}' contains unreadable binary bytes.",
            )

        if not file_truncated:
            try:
                parsed = json.loads(decoded)
                pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                return self._finalize_text(
                    path, size_bytes, pretty, max_chars,
                    extractor="json", file_truncated=False,
                )
            except json.JSONDecodeError:
                pass

        return self._finalize_text(
            path, size_bytes, decoded, max_chars,
            extractor="json_raw_fallback", file_truncated=file_truncated,
        )

    def _extract_docx(self, path: Path, size_bytes: int, max_chars: int) -> ExtractedDocument:
        if size_bytes > MAX_DOCX_BYTES:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="too_large", size_bytes=size_bytes,
                message=(
                    f"DOCX file '{path.name}' is {size_bytes / (1024 * 1024):.1f} MB, "
                    f"exceeding the {MAX_DOCX_BYTES // (1024 * 1024)} MB safety limit."
                ),
            )

        try:
            with open(path, "rb") as f:
                header = f.read(8)
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="error", size_bytes=size_bytes,
                message=f"Could not open '{path.name}': {err}",
            )

        # Encrypted Office documents are wrapped in an OLE2 compound file container
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="encrypted", size_bytes=size_bytes,
                message=f"DOCX file '{path.name}' appears to be encrypted or password-protected and cannot be read.",
            )

        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "word/document.xml" not in zf.namelist():
                    return ExtractedDocument(
                        path=str(path), name=path.name, extension=".docx",
                        success=False, status="corrupted", size_bytes=size_bytes,
                        message=f"DOCX file '{path.name}' is missing 'word/document.xml' and may be corrupted.",
                    )
                xml_bytes = zf.read("word/document.xml")
        except zipfile.BadZipFile:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"DOCX file '{path.name}' is corrupted or not a valid DOCX archive.",
            )
        except RuntimeError as err:
            if "encrypted" in str(err).lower() or "password" in str(err).lower():
                return ExtractedDocument(
                    path=str(path), name=path.name, extension=".docx",
                    success=False, status="encrypted", size_bytes=size_bytes,
                    message=f"DOCX file '{path.name}' is password-protected.",
                )
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"Failed to read DOCX '{path.name}': {err}",
            )

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".docx",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"DOCX XML in '{path.name}' is malformed: {err}",
            )

        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        body = root.find(f"{w_ns}body")
        if body is None:
            body = root

        blocks: List[str] = []
        for child in body:
            tag = child.tag
            if tag == f"{w_ns}p":
                para_text = self._docx_paragraph_text(child, w_ns)
                if para_text:
                    blocks.append(para_text)
            elif tag == f"{w_ns}tbl":
                table_md = self._docx_table_markdown(child, w_ns)
                if table_md:
                    blocks.append(table_md)

        extracted_text = "\n\n".join(blocks)
        return self._finalize_text(
            path, size_bytes, extracted_text, max_chars,
            extractor="docx_xml",
        )

    def _docx_paragraph_text(self, p_elem: ET.Element, w_ns: str) -> str:
        parts: List[str] = []
        style_val = ""
        p_pr = p_elem.find(f"{w_ns}pPr")
        if p_pr is not None:
            p_style = p_pr.find(f"{w_ns}pStyle")
            if p_style is not None:
                style_val = p_style.attrib.get(f"{w_ns}val", "")

        for node in p_elem.iter():
            if node.tag == f"{w_ns}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{w_ns}tab":
                parts.append("\t")
            elif node.tag == f"{w_ns}br":
                parts.append("\n")

        text = "".join(parts).strip()
        if not text:
            return ""

        heading_match = re.match(r"Heading(\d+)", style_val, re.IGNORECASE)
        if heading_match:
            level = min(6, max(1, int(heading_match.group(1))))
            return f"{'#' * level} {text}"
        return text

    def _docx_table_markdown(self, tbl_elem: ET.Element, w_ns: str) -> str:
        rows: List[List[str]] = []
        for tr in tbl_elem.findall(f"{w_ns}tr"):
            cells: List[str] = []
            for tc in tr.findall(f"{w_ns}tc"):
                cell_paras = [
                    self._docx_paragraph_text(p, w_ns).replace("\n", " ")
                    for p in tc.findall(f"{w_ns}p")
                ]
                cells.append(" ".join(c for c in cell_paras if c).strip())
            if any(cells):
                rows.append(cells)

        if not rows:
            return ""
        col_count = max(len(r) for r in rows)
        lines = []
        header = rows[0] + [""] * (col_count - len(rows[0]))
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
        for r in rows[1:]:
            padded = r + [""] * (col_count - len(r))
            lines.append("| " + " | ".join(padded) + " |")
        return "\n".join(lines)

    def _extract_pdf(self, path: Path, size_bytes: int, max_chars: int) -> ExtractedDocument:
        if size_bytes > MAX_PDF_BYTES:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".pdf",
                success=False, status="too_large", size_bytes=size_bytes,
                message=(
                    f"PDF file '{path.name}' is {size_bytes / (1024 * 1024):.1f} MB, "
                    f"exceeding the {MAX_PDF_BYTES // (1024 * 1024)} MB safety limit."
                ),
            )

        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as err:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".pdf",
                success=False, status="error", size_bytes=size_bytes,
                message=f"Could not read PDF '{path.name}': {err}",
            )

        if not raw.lstrip().startswith(b"%PDF-"):
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".pdf",
                success=False, status="corrupted", size_bytes=size_bytes,
                message=f"File '{path.name}' does not have a valid PDF header and may be corrupted.",
            )

        if b"/Encrypt" in raw:
            return ExtractedDocument(
                path=str(path), name=path.name, extension=".pdf",
                success=False, status="encrypted", size_bytes=size_bytes,
                message=f"PDF file '{path.name}' is encrypted or password-protected and cannot be read.",
            )

        # 1. Try optional installed PDF libraries if present in environment
        lib_text, lib_name = self._try_optional_pdf_libs(path)
        if lib_text is not None:
            return self._finalize_text(
                path, size_bytes, lib_text, max_chars,
                extractor=lib_name,
            )

        # 2. Lightweight stdlib PDF content-stream fallback (handles simple text / FlateDecode PDFs)
        fallback_text = self._extract_pdf_streams_stdlib(raw)
        if fallback_text:
            return self._finalize_text(
                path, size_bytes, fallback_text, max_chars,
                extractor="pdf_stdlib_stream",
            )

        return ExtractedDocument(
            path=str(path),
            name=path.name,
            extension=".pdf",
            success=False,
            status="extraction_unavailable",
            size_bytes=size_bytes,
            extractor="none",
            message=(
                f"Could not extract readable text from PDF '{path.name}'. "
                "It may be scanned, image-based, or use compressed fonts, and no dedicated PDF "
                "library (such as pypdf) is installed in the current environment."
            ),
        )

    def _try_optional_pdf_libs(self, path: Path) -> Tuple[Optional[str], str]:
        # Try pypdf / PyPDF2
        for mod_name in ("pypdf", "PyPDF2"):
            try:
                mod = __import__(mod_name)
                reader = mod.PdfReader(str(path))
                if getattr(reader, "is_encrypted", False):
                    return None, mod_name
                pages = []
                for idx, page in enumerate(getattr(reader, "pages", []), start=1):
                    txt = (page.extract_text() or "").strip()
                    if txt:
                        pages.append(f"[Page {idx}]\n{txt}")
                return "\n\n".join(pages), mod_name
            except ImportError:
                continue
            except Exception as err:
                logger.debug("%s failed on %s: %s", mod_name, path.name, err)
                continue

        # Try PyMuPDF (fitz)
        try:
            fitz = __import__("fitz")
            doc = fitz.open(str(path))
            pages = []
            for idx, page in enumerate(doc, start=1):
                txt = (page.get_text("text") or "").strip()
                if txt:
                    pages.append(f"[Page {idx}]\n{txt}")
            doc.close()
            return "\n\n".join(pages), "fitz"
        except ImportError:
            pass
        except Exception as err:
            logger.debug("fitz failed on %s: %s", path.name, err)

        return None, "none"

    def _extract_pdf_streams_stdlib(self, raw: bytes) -> str:
        """Best-effort pure-Python text extraction from uncompressed or FlateDecode PDF streams."""
        chunks: List[str] = []
        stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)

        for match in stream_pattern.finditer(raw):
            stream_bytes = match.group(1)
            candidate_bytes = stream_bytes
            try:
                candidate_bytes = zlib.decompress(stream_bytes)
            except zlib.error:
                pass

            try:
                decoded = candidate_bytes.decode("latin-1")
            except UnicodeDecodeError:
                continue

            if "BT" not in decoded or "ET" not in decoded:
                continue

            for bt_block in re.findall(r"BT(.*?)ET", decoded, re.DOTALL):
                # Extract literal strings from Tj and TJ operators
                for lit in re.findall(r"\((?:\\.|[^\\()])*\)\s*Tj", bt_block):
                    inner = lit[: lit.rfind(")")].lstrip("(")
                    text = self._unescape_pdf_string(inner)
                    if text:
                        chunks.append(text)
                for arr in re.findall(r"\[(.*?)\]\s*TJ", bt_block, re.DOTALL):
                    parts = [
                        self._unescape_pdf_string(s)
                        for s in re.findall(r"\(((?:\\.|[^\\()])*)\)", arr)
                    ]
                    joined = "".join(parts)
                    if joined:
                        chunks.append(joined)

        combined = "\n".join(c.strip() for c in chunks if c.strip()).strip()
        # Require at least a few alphanumeric characters to avoid garbage stream noise
        if sum(ch.isalnum() for ch in combined) < 5:
            return ""
        return combined

    @staticmethod
    def _unescape_pdf_string(text: str) -> str:
        return (
            text.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace("\\(", "(")
            .replace("\\)", ")")
            .replace("\\\\", "\\")
        )


class FileAssistantService:
    """Natural-language AI File Assistant orchestrating search, reading, QA, and comparison."""

    def __init__(
        self,
        file_manager: Optional[FileManager] = None,
        extractor: Optional[DocumentExtractor] = None,
        model: str = "qwen3:4b",
        project_root: Optional[Path] = None,
    ):
        self.file_manager = file_manager or FileManager()
        self.extractor = extractor or DocumentExtractor()
        self.model = model
        self.project_root = (project_root or get_default_project_root()).resolve()

    def _check_emergency_stop(self) -> Optional[str]:
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return f"Execution halted: emergency stop is active ({manager.trigger_reason})."
        return None

    def resolve_directory_alias(self, location_hint: Optional[str]) -> Optional[Path]:
        """Resolve common user folder aliases ('Downloads', 'Documents', 'Desktop', etc.)."""
        if not location_hint:
            return None
        cleaned = location_hint.strip().strip("'\"").lower()
        approved = get_default_approved_directories()
        home = Path.home()
        aliases = {
            "documents": home / "Documents",
            "docs": home / "Documents",
            "downloads": home / "Downloads",
            "desktop": home / "Desktop",
            "pictures": home / "Pictures",
            "music": home / "Music",
            "videos": home / "Videos",
        }
        if cleaned in aliases:
            candidate = aliases[cleaned].resolve()
            if candidate.exists():
                return candidate
        # Try direct path if inside approved dirs
        for d in approved:
            if d.name.lower() == cleaned and d.exists():
                return d
        return Path(location_hint.strip().strip("'\""))

    def validate_readable_path(
        self,
        raw_path: Union[str, Path],
        allow_project_readonly: bool = False,
    ) -> Path:
        """Validate a path for read-only access via FileManager (or read-only project root)."""
        try:
            return self.file_manager.validate_path(raw_path, must_exist=True)
        except PathNotAllowedError:
            if not allow_project_readonly:
                raise
            candidate = Path(raw_path).expanduser().resolve(strict=False)
            if not candidate.exists():
                raise PathNotFoundError(f"Path not found: '{raw_path}'")
            try:
                candidate.relative_to(self.project_root)
            except ValueError as err:
                raise PathNotAllowedError(
                    f"Access denied: '{raw_path}' is outside approved directories."
                ) from err
            # Never allow reading hidden/sensitive dirs inside project
            lower_parts = {p.lower() for p in candidate.parts}
            if lower_parts & {".git", ".venv", "node_modules", "__pycache__"} or candidate.name.startswith(".env"):
                raise PathNotAllowedError(f"Access denied to protected project path: '{raw_path}'")
            return candidate

    def find_files(
        self,
        query: Optional[str] = None,
        extension: Optional[str] = None,
        directory_path: Optional[Union[str, Path]] = None,
        modified_within_days: Optional[int] = None,
        sort_by: str = "modified",
        max_results: int = 25,
        include_project_readonly: bool = False,
    ) -> Dict[str, Any]:
        """Find files across one approved directory (or all approved dirs), with optional recency filtering."""
        halt = self._check_emergency_stop()
        if halt:
            return {"success": False, "status": "cancelled", "message": halt, "items": []}

        if include_project_readonly and directory_path is None:
            search_roots = [self.project_root]
        elif directory_path is not None:
            resolved = self.resolve_directory_alias(str(directory_path)) or Path(directory_path)
            search_roots = [resolved]
        else:
            search_roots = [d for d in self.file_manager.approved_directories if d.exists()]

        cutoff_ts: Optional[float] = None
        if modified_within_days is not None and modified_within_days >= 0:
            cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=modified_within_days)).timestamp()

        collected: List[FileInfo] = []
        seen_paths: set[str] = set()

        for root in search_roots:
            try:
                if include_project_readonly and root.resolve() == self.project_root:
                    items = self._search_project_readonly(
                        root, query=query, extension=extension, max_results=max_results
                    )
                else:
                    items = self.file_manager.search_files(
                        dir_path=root,
                        query=query,
                        extension=extension,
                        max_depth=5,
                        max_results=max_results,
                        modified_within_days=modified_within_days,
                        sort_by=sort_by,
                    )
                for item in items:
                    if cutoff_ts is not None:
                        try:
                            item_dt = datetime.fromisoformat(item.modified_at)
                            if item_dt.timestamp() < cutoff_ts:
                                continue
                        except ValueError:
                            pass
                    if item.path not in seen_paths:
                        seen_paths.add(item.path)
                        collected.append(item)
            except (PathNotAllowedError, PathTraversalError, PathNotFoundError, FileManagerError) as err:
                if len(search_roots) == 1:
                    return {
                        "success": False,
                        "status": "not_allowed" if isinstance(err, (PathNotAllowedError, PathTraversalError)) else "error",
                        "message": str(err),
                        "items": [],
                    }
                continue

        if sort_by == "modified":
            collected.sort(key=lambda i: i.modified_at, reverse=True)
        else:
            collected.sort(key=lambda i: i.name.lower())

        collected = collected[:max_results]
        items_dicts = [i.to_dict() for i in collected]
        if not items_dicts:
            desc = query or (f"*{extension}" if extension else "files")
            return {
                "success": True,
                "status": "not_found",
                "message": f"No matching files found for '{desc}'.",
                "items": [],
                "count": 0,
            }

        return {
            "success": True,
            "status": "success",
            "message": self._format_found_files_message(items_dicts, query=query, extension=extension),
            "items": items_dicts,
            "count": len(items_dicts),
        }

    def _search_project_readonly(
        self,
        root: Path,
        query: Optional[str] = None,
        extension: Optional[str] = None,
        max_results: int = 50,
    ) -> List[FileInfo]:
        norm_query = query.strip().lower() if query else None
        norm_ext = extension.strip().lower() if extension else None
        if norm_ext and not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"

        skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "build"}
        results: List[FileInfo] = []

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs and not d.startswith(".")]
            for fn in filenames:
                if len(results) >= max_results:
                    return results
                if norm_query and norm_query not in fn.lower():
                    continue
                if norm_ext and Path(fn).suffix.lower() != norm_ext:
                    continue
                fp = Path(dirpath) / fn
                try:
                    stat = fp.stat()
                    results.append(
                        FileInfo(
                            name=fp.name,
                            path=str(fp),
                            size_bytes=stat.st_size,
                            is_directory=False,
                            extension=fp.suffix.lower(),
                            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        )
                    )
                except OSError:
                    continue
        return results

    def _format_found_files_message(
        self,
        items: List[Dict[str, Any]],
        query: Optional[str] = None,
        extension: Optional[str] = None,
    ) -> str:
        header = f"Found {len(items)} matching file(s)"
        if query:
            header += f" matching '{query}'"
        elif extension:
            header += f" with extension '{extension}'"
        lines = [f"{header}:"]
        for idx, item in enumerate(items[:15], start=1):
            size_kb = max(1, (item.get("size_bytes", 0) + 1023) // 1024) if item.get("size_bytes", 0) > 0 else 0
            mod_str = (item.get("modified_at") or "")[:19].replace("T", " ")
            lines.append(f"{idx}. {item['name']} ({size_kb} KB, modified {mod_str}) — {item['path']}")
        if len(items) > 15:
            lines.append(f"...and {len(items) - 15} more.")
        return "\n".join(lines)

    def read_document(
        self,
        file_path: Union[str, Path],
        max_chars: int = DEFAULT_MAX_CHARS,
        allow_project_readonly: bool = False,
    ) -> ExtractedDocument:
        """Validate path and extract document text safely."""
        try:
            validated = self.validate_readable_path(file_path, allow_project_readonly=allow_project_readonly)
        except (PathNotAllowedError, PathTraversalError) as err:
            return ExtractedDocument(
                path=str(file_path), name=Path(file_path).name,
                extension=Path(file_path).suffix.lower(),
                success=False, status="not_allowed", message=str(err),
            )
        except PathNotFoundError as err:
            return ExtractedDocument(
                path=str(file_path), name=Path(file_path).name,
                extension=Path(file_path).suffix.lower(),
                success=False, status="not_found", message=str(err),
            )
        except FileManagerError as err:
            return ExtractedDocument(
                path=str(file_path), name=Path(file_path).name,
                extension=Path(file_path).suffix.lower(),
                success=False, status="error", message=str(err),
            )

        return self.extractor.extract(validated, max_chars=max_chars)

    def summarize_document(
        self,
        file_path: Union[str, Path],
        allow_project_readonly: bool = False,
    ) -> Dict[str, Any]:
        """Extract text from a local document and summarize it via local Ollama."""
        halt = self._check_emergency_stop()
        if halt:
            return {"success": False, "status": "cancelled", "message": halt}

        doc = self.read_document(file_path, allow_project_readonly=allow_project_readonly)
        if not doc.success:
            return {"success": False, "status": doc.status, "message": doc.message, "document": doc.to_dict()}
        if doc.is_empty:
            return {
                "success": True,
                "status": "empty",
                "message": f"'{doc.name}' ({doc.path}) is empty, so there is no content to summarize.",
                "document": doc.to_dict(),
            }

        system_prompt = (
            "You are VoxPilot's local AI File Assistant. Summarize the provided document accurately and concisely "
            "using ONLY facts present in the document. Do not invent details. Include key points and structure."
        )
        user_prompt = (
            f"Document Name: {doc.name}\n"
            f"Document Path: {doc.path}\n"
            f"<document>\n{doc.text}\n</document>\n\n"
            "Provide a clear summary with bulleted key takeaways."
        )
        return self._run_grounded_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            docs=[doc],
            operation="summarize_document",
        )

    def answer_document_question(
        self,
        file_path: Union[str, Path],
        question: str,
        allow_project_readonly: bool = False,
    ) -> Dict[str, Any]:
        """Answer a user question strictly grounded in the extracted document text."""
        halt = self._check_emergency_stop()
        if halt:
            return {"success": False, "status": "cancelled", "message": halt}

        doc = self.read_document(file_path, allow_project_readonly=allow_project_readonly)
        if not doc.success:
            return {"success": False, "status": doc.status, "message": doc.message, "document": doc.to_dict()}
        if doc.is_empty:
            return {
                "success": True,
                "status": "empty",
                "message": f"'{doc.name}' ({doc.path}) is empty, so it does not contain information to answer your question.",
                "document": doc.to_dict(),
            }

        system_prompt = (
            "You are VoxPilot's local AI File Assistant. Answer the user's question using ONLY the provided "
            "document content. If the document does not mention the answer, explicitly state that the document "
            "does not contain that information. Never fabricate facts."
        )
        user_prompt = (
            f"Document Name: {doc.name}\n"
            f"Document Path: {doc.path}\n"
            f"<document>\n{doc.text}\n</document>\n\n"
            f"Question: {question}"
        )
        return self._run_grounded_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            docs=[doc],
            operation="answer_document_question",
        )

    def compare_documents(
        self,
        file_paths: Sequence[Union[str, Path]],
        focus: Optional[str] = None,
        allow_project_readonly: bool = False,
    ) -> Dict[str, Any]:
        """Compare 2 or more local documents using local Ollama."""
        halt = self._check_emergency_stop()
        if halt:
            return {"success": False, "status": "cancelled", "message": halt}

        if len(file_paths) < 2:
            return {
                "success": False,
                "status": "validation_failed",
                "message": "Please specify at least two documents to compare.",
            }

        docs: List[ExtractedDocument] = []
        for fp in file_paths[:4]:
            doc = self.read_document(
                fp, max_chars=DEFAULT_COMPARE_MAX_CHARS,
                allow_project_readonly=allow_project_readonly,
            )
            if not doc.success:
                return {
                    "success": False,
                    "status": doc.status,
                    "message": f"Cannot compare documents — {doc.message}",
                    "documents": [d.to_dict() for d in docs] + [doc.to_dict()],
                }
            docs.append(doc)

        doc_blocks = []
        for idx, d in enumerate(docs, start=1):
            content_or_empty = d.text if not d.is_empty else "[EMPTY FILE]"
            doc_blocks.append(
                f"Document {idx}: {d.name} ({d.path})\n<document_{idx}>\n{content_or_empty}\n</document_{idx}>"
            )

        system_prompt = (
            "You are VoxPilot's local AI File Assistant. Compare the provided documents strictly based on "
            "their contents. Highlight key similarities, differences, additions/removals, or conflicting details. "
            "Do not invent facts not present in the documents."
        )
        focus_line = f"\nComparison focus: {focus}" if focus else ""
        user_prompt = "\n\n".join(doc_blocks) + focus_line + "\n\nProvide a structured comparison."
        return self._run_grounded_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            docs=docs,
            operation="compare_documents",
        )

    def _run_grounded_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        docs: List[ExtractedDocument],
        operation: str,
    ) -> Dict[str, Any]:
        truncation_notes = [
            f"'{d.name}' ({d.included_chars} of {d.total_chars} chars)"
            for d in docs if d.was_truncated
        ]
        truncation_banner = ""
        if truncation_notes:
            truncation_banner = (
                "\n\nNote: Content was truncated due to local context limits for: "
                + ", ".join(truncation_notes)
                + "."
            )

        try:
            from backend.ai.providers.runtime import run_provider_chat

            def _local_file_call(local_model: str, msgs, temp: float) -> str:
                model_manager = get_shared_model_manager()
                target_model = self.model or local_model
                with model_manager.track_inference(target_model):
                    response = ollama.chat(
                        model=target_model,
                        messages=msgs,
                        options={"temperature": temp},
                    )
                return (getattr(getattr(response, "message", None), "content", None) or "").strip()

            answer = run_provider_chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                local_executor=_local_file_call,
            ).strip()
            if not answer:
                return {
                    "success": False,
                    "status": "llm_error",
                    "operation": operation,
                    "message": "The local AI model returned an empty response.",
                    "documents": [d.to_dict() for d in docs],
                }

            return {
                "success": True,
                "status": "success",
                "operation": operation,
                "message": f"{answer}{truncation_banner}",
                "was_truncated": bool(truncation_notes),
                "documents": [d.to_dict() for d in docs],
            }
        except Exception as err:
            logger.warning("FileAssistant Ollama inference failed (%s): %s", operation, err)
            return {
                "success": False,
                "status": "llm_unavailable",
                "operation": operation,
                "message": (
                    f"Local AI model ({self.model}) is currently unavailable to process "
                    f"{', '.join(d.name for d in docs)}: {err}"
                ),
                "was_truncated": bool(truncation_notes),
                "documents": [d.to_dict() for d in docs],
            }

    def plan_folder_organization(
        self,
        directory_path: Union[str, Path],
    ) -> Dict[str, Any]:
        """Build a preview plan to organize files in an approved directory into category subfolders."""
        halt = self._check_emergency_stop()
        if halt:
            return {"success": False, "status": "cancelled", "message": halt, "moves": []}

        resolved = self.resolve_directory_alias(str(directory_path)) or Path(directory_path)
        try:
            items = self.file_manager.list_directory(resolved, show_hidden=False)
        except (PathNotAllowedError, PathTraversalError, PathNotFoundError, FileManagerError) as err:
            return {"success": False, "status": "error", "message": str(err), "moves": []}

        files = [i for i in items if not i.is_directory]
        if not files:
            return {
                "success": True,
                "status": "empty",
                "message": f"No files to organize in '{resolved}'.",
                "moves": [],
            }

        moves: List[Dict[str, str]] = []
        for f in files:
            ext = f.extension.lower()
            category = "Other"
            for cat_name, exts in DEFAULT_ORGANIZATION_CATEGORIES.items():
                if ext in exts:
                    category = cat_name
                    break
            dest_folder = str(Path(resolved) / category)
            dest_file = str(Path(dest_folder) / f.name)
            moves.append({
                "source_path": f.path,
                "destination_folder": dest_folder,
                "destination_path": dest_file,
                "category": category,
                "file_name": f.name,
            })

        return {
            "success": True,
            "status": "confirmation_required",
            "requires_confirmation": True,
            "directory": str(resolved),
            "file_count": len(moves),
            "moves": moves,
            "message": (
                f"Prepared organization plan for {len(moves)} file(s) in '{resolved}' "
                f"into category folders. Confirmation is required before moving files."
            ),
        }


_shared_file_assistant: Optional[FileAssistantService] = None


def get_shared_file_assistant() -> FileAssistantService:
    """Return a singleton FileAssistantService instance."""
    global _shared_file_assistant
    if _shared_file_assistant is None:
        _shared_file_assistant = FileAssistantService()
    return _shared_file_assistant
