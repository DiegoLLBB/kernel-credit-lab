"""Offline EDGAR client tests (fixtures under tests/fixtures/edgar/)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.models import Filing, Issuer
from app.sources.edgar import EdgarClient, EdgarError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"

live = pytest.mark.skipif(
    os.getenv("EDGAR_LIVE") != "1",
    reason="set EDGAR_LIVE=1 to hit data.sec.gov (not used in CI)",
)


def _settings() -> Settings:
    return Settings(sec_user_agent="Kernel Credit Lab tests@example.com")


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _mock_transport() -> httpx.MockTransport:
    mapping = {
        "https://data.sec.gov/submissions/CIK0000732712.json": "verizon_submissions.json",
        "https://data.sec.gov/submissions/CIK0000732712-submissions-001.json": (
            "CIK0000732712-submissions-001.json"
        ),
        "https://data.sec.gov/submissions/CIK0001326160.json": "duke_submissions.json",
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/"
        "0001193125-26-273410-index.htm": "verizon_0001193125-26-273410-index.htm",
        "https://www.sec.gov/Archives/edgar/data/1326160/000132616026000026/"
        "0001326160-26-000026-index.htm": "duke_0001326160-26-000026-index.htm",
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/"
        "d15574dex991.htm": "verizon_ex99_1.htm",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers.get("user-agent")
        name = mapping.get(url)
        if name is None:
            return httpx.Response(404, text=f"missing fixture for {url}")
        body = _fixture_bytes(name)
        ctype = "application/json" if name.endswith(".json") else "text/html"
        return httpx.Response(200, content=body, headers={"content-type": ctype})

    return httpx.MockTransport(handler)


@pytest.fixture()
def edgar() -> EdgarClient:
    client = EdgarClient(settings=_settings(), transport=_mock_transport(), sleeper=lambda _s: None)
    yield client
    client.close()


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_requires_real_user_agent() -> None:
    with pytest.raises(EdgarError, match="SEC_USER_AGENT"):
        EdgarClient(settings=Settings(sec_user_agent=""))
    with pytest.raises(EdgarError, match="placeholder"):
        EdgarClient(settings=Settings(sec_user_agent="KernelCreditLab your-email@example.com"))


def test_list_filings_verizon_8k(edgar: EdgarClient) -> None:
    filings = edgar.list_filings(
        "732712",
        forms=["8-K"],
        since=date(2026, 6, 1),
        until=date(2026, 6, 30),
    )
    by_acc = {f.accession: f for f in filings}
    assert "0001193125-26-273410" in by_acc
    ref = by_acc["0001193125-26-273410"]
    assert ref.form == "8-K"
    assert ref.filing_date == date(2026, 6, 17)
    assert ref.primary_document == "d15574d8k.htm"
    assert ref.url.endswith("/d15574d8k.htm")


def test_list_filings_reads_older_files_page(edgar: EdgarClient) -> None:
    filings = edgar.list_filings(
        "732712",
        forms=["8-K", "10-Q"],
        since=date(2020, 1, 1),
        until=date(2020, 12, 31),
    )
    accessions = {f.accession for f in filings}
    assert "0001193125-20-000001" in accessions
    assert "0001193125-20-000002" in accessions


def test_list_filings_duke_10q(edgar: EdgarClient) -> None:
    filings = edgar.list_filings(
        "1326160",
        forms=["10-Q"],
        since=date(2026, 5, 1),
        until=date(2026, 5, 31),
    )
    by_acc = {f.accession: f for f in filings}
    assert "0001326160-26-000026" in by_acc
    ref = by_acc["0001326160-26-000026"]
    assert ref.filing_date == date(2026, 5, 5)
    assert ref.primary_document == "duk-20260331.htm"


def test_filing_documents_verizon_index(edgar: EdgarClient) -> None:
    docs = edgar.filing_documents("732712", "0001193125-26-273410")
    by_type = {d.type: d for d in docs}
    assert "EX-99.1" in by_type
    assert by_type["EX-99.1"].name == "d15574dex991.htm"
    assert by_type["EX-99.1"].url.endswith("/d15574dex991.htm")
    assert any(d.type == "8-K" for d in docs)


def test_fetch_text_keeps_table_rows(edgar: EdgarClient) -> None:
    text = edgar.fetch_text(
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/d15574dex991.htm"
    )
    assert "Exhibit 99.1" in text or "EX-99.1" in text or "News Release" in text
    # Table cells from the press-release header stay on one line.
    assert any(
        "FOR IMMEDIATE RELEASE" in line and "Media contact" in line for line in text.splitlines()
    )


def test_upsert_filings_idempotent(edgar: EdgarClient, session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()
    filings = edgar.list_filings(
        "732712", forms=["8-K"], since=date(2026, 6, 17), until=date(2026, 6, 17)
    )
    assert filings
    first = edgar.upsert_filings(session, issuer, filings)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Filing)) == len(first)

    second = edgar.upsert_filings(session, issuer, filings)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Filing)) == len(first)
    assert {r.accession_number for r in second} == {r.accession_number for r in first}
    row = session.scalars(
        select(Filing).where(Filing.accession_number == "0001193125-26-273410")
    ).one()
    assert row.issuer_id == issuer.id
    assert row.form_type == "8-K"
    assert row.filed_at == date(2026, 6, 17)


def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow")
        body = _fixture_bytes("verizon_submissions.json")
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with EdgarClient(
        settings=_settings(),
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    ) as client:
        filings = client.list_filings(
            "732712", forms=["8-K"], since=date(2026, 6, 17), until=date(2026, 6, 17)
        )
    assert calls["n"] == 3
    assert any(f.accession == "0001193125-26-273410" for f in filings)


@live
def test_live_verizon_8k_june_2026() -> None:
    """Optional live check against data.sec.gov (skipped in CI)."""
    with EdgarClient(settings=get_settings_live()) as client:
        filings = client.list_filings(
            "732712", forms=["8-K"], since=date(2026, 6, 17), until=date(2026, 6, 17)
        )
    assert any(f.accession == "0001193125-26-273410" for f in filings)


def get_settings_live() -> Settings:
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    if not s.sec_user_agent:
        pytest.skip("SEC_USER_AGENT not configured")
    return s
