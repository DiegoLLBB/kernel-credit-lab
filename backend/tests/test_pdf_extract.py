"""PDF text extraction for replay source ingest."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.ingest.pdf import document_bytes_to_text, looks_like_pdf, pdf_bytes_to_text
from app.ingest.replay import _store_filing
from app.models import Filing, Issuer
from app.sources.newsroom import NewsroomClient

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replay" / "sample_debt_details.pdf"
PDF_URL = "https://example.com/Debt-Details-06-30-26-FINAL.pdf"


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_looks_like_pdf_by_magic_and_url() -> None:
    data = FIXTURE.read_bytes()
    assert looks_like_pdf(data, content_type="application/pdf")
    assert looks_like_pdf(data, url=PDF_URL)
    assert not looks_like_pdf(b"<html>hi</html>", content_type="text/html", url="https://x/a.htm")


def test_pdf_fixture_extracts_table_lines() -> None:
    data = FIXTURE.read_bytes()
    text = pdf_bytes_to_text(data)
    assert "duk_tl_2026" in text
    assert "200,000,000" in text
    assert "dec_tl_2027" in text
    # Prefer one instrument per line (table-row style).
    assert "\n" in text or " | " in text


def test_store_filing_strips_nul_bytes(session: Session) -> None:
    issuer = Issuer(key="duke_corp", name="Duke Energy Corporation")
    session.add(issuer)
    session.flush()
    filing = _store_filing(
        session,
        issuer=issuer,
        url="https://example.com/with-nul.txt",
        form_type="debt_details",
        filed_at=date(2026, 6, 30),
        title="Debt details",
        text="hello\x00world\x00",
    )
    session.commit()
    row = session.get(Filing, filing.id)
    assert row is not None
    assert row.body_text == "helloworld"
    assert "\x00" not in (row.body_text or "")


def test_newsroom_fetch_text_extracts_pdf() -> None:
    data = FIXTURE.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404, text="missing")
        assert url == PDF_URL
        return httpx.Response(
            200,
            content=data,
            headers={"content-type": "application/pdf"},
        )

    client = NewsroomClient(
        settings=Settings(sec_user_agent="Kernel Credit Lab tests@kernel.example.edu"),
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    try:
        text = client.fetch_text(PDF_URL)
    finally:
        client.close()
    assert "duk_tl_2026" in text
    assert "\x00" not in text


def test_document_bytes_to_text_html_passthrough() -> None:
    raw = b"<html><body>plain note</body></html>"
    text = document_bytes_to_text(raw, content_type="text/html; charset=utf-8")
    assert "plain note" in text or "<body>" in text
