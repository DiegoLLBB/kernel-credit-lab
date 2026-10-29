"""Extract plain text from PDF bytes for filing storage / agent read_document."""

from __future__ import annotations

import io
import logging
from urllib.parse import urlparse

logger = logging.getLogger("kernel.ingest.pdf")

PDF_MAGIC = b"%PDF-"


def looks_like_pdf(
    data: bytes,
    *,
    content_type: str | None = None,
    url: str | None = None,
) -> bool:
    """True when content-type, URL path, or magic header says PDF."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in {"application/pdf", "application/x-pdf"}:
        return True
    if url:
        path = urlparse(url).path.lower()
        if path.endswith(".pdf"):
            return True
    head = data[:1024].lstrip() if data else b""
    return head.startswith(PDF_MAGIC)


def _table_rows_as_lines(page: object) -> list[str]:
    """One text line per table row when pdfplumber finds tables."""
    lines: list[str] = []
    extract_tables = getattr(page, "extract_tables", None)
    if not callable(extract_tables):
        return lines
    try:
        tables = extract_tables() or []
    except Exception:  # noqa: BLE001 — best-effort; fall back to page text
        return lines
    for table in tables:
        for row in table or []:
            cells = [str(c).strip() if c is not None else "" for c in row]
            line = " | ".join(c for c in cells if c)
            if line:
                lines.append(line)
    return lines


def pdf_bytes_to_text(data: bytes) -> str:
    """Extract text from a PDF; prefer one line per table row via pdfplumber."""
    if not data:
        return ""
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None  # type: ignore[assignment]

    if pdfplumber is not None:
        try:
            parts: list[str] = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    table_lines = _table_rows_as_lines(page)
                    page_text = (page.extract_text() or "").strip()
                    if table_lines:
                        parts.append("\n".join(table_lines))
                        # Keep non-table prose if extract_text has more than the rows.
                        if page_text and len(page_text) > sum(len(t) for t in table_lines):
                            parts.append(page_text)
                    elif page_text:
                        parts.append(page_text)
            text = "\n\n".join(p for p in parts if p).strip()
            if text:
                return text
        except Exception:
            logger.exception("pdfplumber_extract_failed")

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pdfplumber or pypdf; install pdfplumber"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append((page.extract_text() or "").strip())
    return "\n\n".join(p for p in pages if p).strip()


def document_bytes_to_text(
    data: bytes,
    *,
    content_type: str | None = None,
    url: str | None = None,
) -> str:
    """Decode HTTP body to plain text; PDFs go through ``pdf_bytes_to_text``."""
    if looks_like_pdf(data, content_type=content_type, url=url):
        return pdf_bytes_to_text(data)

    # Prefer charset from content-type; fall back to utf-8 then latin-1.
    charset = "utf-8"
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip().strip("\"'")
    try:
        return data.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")
