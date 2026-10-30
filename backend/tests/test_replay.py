"""Offline replay driver tests (no network)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.ingest.replay import (
    AGENT_FORBIDDEN_FIELDS,
    DEFAULT_REPLAY_PATH,
    ReplayClients,
    ReplayValidationError,
    advance_to,
    agent_visible_payload,
    extract_document_title,
    load_replay_events,
    maturity_summary,
    neutral_summary,
    parse_edgar_index_url,
    resolve_issuer,
    select_index_documents,
    step_to_next,
)
from app.models import (
    Event,
    EventFiling,
    Filing,
    Instrument,
    Issuer,
    RateObservation,
    ReplayExpectation,
)
from app.sources.edgar import DocumentRef, EdgarClient
from scripts.load_seed import load_seed

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLAY_JSON = FIXTURES / "replay" / "minimal_events.json"
INTEL_JSON = FIXTURES / "replay" / "intel_notes_apr.json"
DUKE_Q1_JSON = FIXTURES / "replay" / "duke_q1_10q.json"
DUKE_Q1_MERGED_JSON = FIXTURES / "replay" / "duke_q1_merged.json"
VERIZON_8K_JSON = FIXTURES / "replay" / "verizon_8k_frontier.json"
NEWSROOM = FIXTURES / "newsroom"
EDGAR_FIX = FIXTURES / "edgar"
SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"

TENDER_RESULTS_URL = "https://www.verizon.com/about/node/929969"
TENDER_LAUNCH_URL = (
    "https://www.verizon.com/about/news/feed/verizon-debt-tender-offer-consent-solicitation"
)
INTC_8K = (
    "https://www.sec.gov/Archives/edgar/data/50863/000119312526197845/"
    "0001193125-26-197845-index.htm"
)
INTC_10Q = (
    "https://www.sec.gov/Archives/edgar/data/50863/000005086326000157/"
    "0000050863-26-000157-index.htm"
)


class FakeFetcher:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"unexpected fetch {url}")
        return self.mapping[url]


def _edgar_fixture_transport() -> httpx.MockTransport:
    mapping = {
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/"
        "0001193125-26-273410-index.htm": "verizon_0001193125-26-273410-index.htm",
        "https://www.sec.gov/Archives/edgar/data/1326160/000132616026000026/"
        "0001326160-26-000026-index.htm": "duke_0001326160-26-000026-index.htm",
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/"
        "d15574dex991.htm": "verizon_ex99_1.htm",
        "https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/"
        "d15574d8k.htm": "verizon_d15574d8k.htm",
        "https://www.sec.gov/Archives/edgar/data/17797/000132616026000026/"
        "duk-20260331.htm": "duke_duk-20260331.htm",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        name = mapping.get(url)
        if name is None:
            return httpx.Response(404, text=f"missing fixture for {url}")
        body = (EDGAR_FIX / name).read_bytes()
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


@pytest.fixture()
def edgar_client() -> EdgarClient:
    client = EdgarClient(
        settings=Settings(sec_user_agent="Kernel Credit Lab tests@kernel.example.edu"),
        transport=_edgar_fixture_transport(),
        sleeper=lambda _s: None,
    )
    yield client
    client.close()


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def verizon(session: Session) -> Issuer:
    issuer = Issuer(key="verizon", name="Verizon Communications Inc.", cik="732712", ticker="VZ")
    session.add(issuer)
    session.flush()
    return issuer


@pytest.fixture()
def intel(session: Session) -> Issuer:
    issuer = Issuer(key="intel", name="Intel Corporation", cik="50863", ticker="INTC")
    session.add(issuer)
    session.flush()
    return issuer


@pytest.fixture()
def duke_corp(session: Session) -> Issuer:
    issuer = Issuer(key="duke_corp", name="Duke Energy Corporation", cik="1326160", ticker="DUK")
    session.add(issuer)
    session.flush()
    return issuer


@pytest.fixture()
def clients() -> ReplayClients:
    launch = (NEWSROOM / "verizon_tender_launch_2026-05-11.html").read_text(
        encoding="utf-8", errors="replace"
    )
    results = (NEWSROOM / "verizon_tender_results_2026-06-17.html").read_text(
        encoding="utf-8", errors="replace"
    )
    newsroom = FakeFetcher({TENDER_LAUNCH_URL: launch, TENDER_RESULTS_URL: results})
    edgar = FakeFetcher({})
    return ReplayClients(edgar=edgar, newsroom=newsroom)


def test_load_requires_issuer_in_db(session: Session) -> None:
    with pytest.raises(ReplayValidationError, match="not found in DB"):
        load_replay_events(session, REPLAY_JSON)


def test_load_upserts_expectations_not_events(session: Session, verizon: Issuer) -> None:
    rows = load_replay_events(session, REPLAY_JSON)
    session.commit()
    assert {r.key for r in rows} == {"vz_tender_launch", "vz_tender_results"}
    assert session.scalar(select(func.count()).select_from(Event)) == 0
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.key == "vz_tender_results")
    ).one()
    assert exp.expected_alert is True
    assert exp.expected_changes
    assert exp.why
    assert exp.available_from == date(2026, 6, 17)


def test_tender_results_created_on_june_17_not_before(
    session: Session, verizon: Issuer, clients: ReplayClients
) -> None:
    load_replay_events(session, REPLAY_JSON)
    session.commit()

    before = advance_to(session, date(2026, 6, 16), clients)
    session.commit()
    keys_before = {e.replay_key for e in before}
    assert "vz_tender_launch" in keys_before
    assert "vz_tender_results" not in keys_before
    assert (
        session.scalars(select(Event).where(Event.replay_key == "vz_tender_results")).first()
        is None
    )

    created = advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    assert any(e.replay_key == "vz_tender_results" for e in created)
    event = session.scalars(select(Event).where(Event.replay_key == "vz_tender_results")).one()
    assert event.status == "pending"
    assert event.event_type == "press_release"
    assert event.event_date == date(2026, 6, 17)
    assert event.summary == "Verizon Communications Inc., press release, 17 Jun 2026"
    assert event.source_filing_id is not None
    filing = session.get(Filing, event.source_filing_id)
    assert filing is not None
    assert filing.url == TENDER_RESULTS_URL
    assert filing.content_hash
    assert filing.form_type == "press_release"
    # Document title from HTML <title>, not sources[].description
    assert filing.title is not None
    assert "Verizon announces expiration and final results" in filing.title
    assert filing.title != "Final results"
    # Regression: advance_to must store fetched body_text (agent reads this).
    assert filing.body_text
    assert "Verizon" in filing.body_text
    assert len(filing.body_text) > 100
    links = session.scalars(select(EventFiling).where(EventFiling.event_id == event.id)).all()
    assert len(links) == 1
    assert links[0].filing_id == filing.id


def test_advance_to_stores_body_text_with_fake_clients(
    session: Session, verizon: Issuer, clients: ReplayClients
) -> None:
    """Filings created by advance_to keep the fetched document text."""
    load_replay_events(session, REPLAY_JSON)
    session.commit()
    advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    for key in ("vz_tender_launch", "vz_tender_results"):
        event = session.scalars(select(Event).where(Event.replay_key == key)).one()
        filing = session.get(Filing, event.source_filing_id)
        assert filing is not None
        assert filing.body_text, f"{key} missing body_text"
        assert filing.content_hash
        assert hashlib.sha256(filing.body_text.encode("utf-8")).hexdigest() == filing.content_hash


def test_advance_idempotent(session: Session, verizon: Issuer, clients: ReplayClients) -> None:
    load_replay_events(session, REPLAY_JSON)
    first = advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    n_events = session.scalar(select(func.count()).select_from(Event))
    n_filings = session.scalar(select(func.count()).select_from(Filing))
    second = advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    assert second == []
    assert session.scalar(select(func.count()).select_from(Event)) == n_events
    assert session.scalar(select(func.count()).select_from(Filing)) == n_filings
    assert len(first) >= 2


def test_agent_visible_payload_leaks_no_expectation_text(
    session: Session, verizon: Issuer, clients: ReplayClients
) -> None:
    load_replay_events(session, REPLAY_JSON)
    advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    event = session.scalars(select(Event).where(Event.replay_key == "vz_tender_results")).one()
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.key == "vz_tender_results")
    ).one()
    payload = agent_visible_payload(event)
    assert "replay_key" not in payload
    for field in AGENT_FORBIDDEN_FIELDS:
        assert field not in payload

    forbidden_snippets = [
        exp.key,
        exp.title,
        exp.why or "",
        *(str(s.get("description") or "") for s in (exp.sources or [])),
    ]
    forbidden_snippets = [s for s in forbidden_snippets if s]
    blob = " | ".join(str(v) for v in payload.values() if v is not None)
    for snippet in forbidden_snippets:
        assert snippet not in blob, f"payload leaked {snippet!r}"


def test_intel_apr_excludes_july_10q(session: Session, intel: Issuer) -> None:
    eight_k = "<html><head><title>Intel Form 8-K</title></head><body>notes</body></html>"
    ten_q = "<html><head><title>Intel Form 10-Q</title></head><body>bridge</body></html>"
    clients = ReplayClients(
        edgar=FakeFetcher({INTC_8K: eight_k, INTC_10Q: ten_q}),
        newsroom=FakeFetcher({}),
    )
    load_replay_events(session, INTEL_JSON)
    session.commit()
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.key == "intc_notes_apr")
    ).one()
    assert len(exp.sources) == 1
    assert exp.sources[0]["form"] == "8-K"
    assert len(exp.confirming_sources) == 1
    assert exp.confirming_sources[0]["form"] == "10-Q"
    assert exp.confirming_sources[0]["filed"] == "2026-07-24"
    assert exp.notes

    created = advance_to(session, date(2026, 4, 30), clients)
    session.commit()
    event = next(e for e in created if e.replay_key == "intc_notes_apr")
    assert event.event_type == "8-K"
    assert event.event_date == date(2026, 4, 30)
    assert event.summary == "Intel Corporation, 8-K filed 30 Apr 2026"

    linked_urls = {
        session.get(Filing, link.filing_id).url  # type: ignore[union-attr]
        for link in session.scalars(
            select(EventFiling).where(EventFiling.event_id == event.id)
        ).all()
    }
    assert linked_urls == {INTC_8K}
    assert INTC_10Q not in linked_urls
    assert session.scalars(select(Filing).where(Filing.url == INTC_10Q)).first() is None
    assert INTC_10Q not in clients.edgar.calls


def test_maturity_and_rate_update_events(session: Session, verizon: Issuer) -> None:
    session.add(
        Instrument(
            key="vz_demo_2026",
            issuer_id=verizon.id,
            name="Demo Notes due 2026",
            coupon=Decimal("3.000"),
            maturity=date(2026, 3, 20),
            amount_outstanding=Decimal("1000000"),
            status="active",
        )
    )
    session.add(
        RateObservation(series_id="DGS5", obs_date=date(2026, 3, 19), value=Decimal("4.00"))
    )
    session.flush()
    clients = ReplayClients(edgar=FakeFetcher({}), newsroom=FakeFetcher({}))
    created = advance_to(session, date(2026, 3, 20), clients, window_start=date(2026, 1, 1))
    session.commit()
    by_key = {e.replay_key: e for e in created}
    assert "maturity:vz_demo_2026:2026-03-20" in by_key
    mat = by_key["maturity:vz_demo_2026:2026-03-20"]
    assert mat.event_type == "maturity"
    assert mat.summary == (
        "Verizon Communications Inc., 3.000% Notes due 2026 ($1m) mature 20 Mar 2026"
    )
    assert "vz_demo_2026" not in (mat.summary or "")
    assert "rate_update:2026-03-20" in by_key
    assert by_key["rate_update:2026-03-20"].summary == "Rate update, 20 Mar 2026"


def test_duke_maturity_summary_names_instrument(session: Session) -> None:
    duke = Issuer(key="duke_corp", name="Duke Energy Corporation", cik="1326160", ticker="DUK")
    session.add(duke)
    session.flush()
    inst = Instrument(
        key="duk_2650_2026",
        issuer_id=duke.id,
        name="2.650% Senior Notes due 2026",
        coupon=Decimal("2.650"),
        maturity=date(2026, 9, 1),
        amount_outstanding=Decimal("1500000000"),
        status="active",
    )
    session.add(inst)
    session.flush()
    assert maturity_summary(issuer_name=duke.name, instrument=inst) == (
        "Duke Energy Corporation, 2.650% Notes due 2026 ($1.5bn) mature 1 Sep 2026"
    )
    clients = ReplayClients(edgar=FakeFetcher({}), newsroom=FakeFetcher({}))
    created = advance_to(session, date(2026, 9, 1), clients, window_start=date(2026, 9, 1))
    session.commit()
    mat = next(e for e in created if e.event_type == "maturity")
    assert mat.summary == (
        "Duke Energy Corporation, 2.650% Notes due 2026 ($1.5bn) mature 1 Sep 2026"
    )


def test_bad_filed_date_is_replay_validation_error(
    session: Session, verizon: Issuer, tmp_path: Path
) -> None:
    payload = {
        "window": {"from": "2026-01-01", "to": "2026-12-31"},
        "events": [
            {
                "key": "bad_filed",
                "issuer": "verizon",
                "event_type": "press_release",
                "event_date": "2026-06-17",
                "available_from": "2026-06-17",
                "title": "Should not leak",
                "sources": [
                    {
                        "form": "press_release",
                        "filed": "17 June 2026",
                        "url": "https://www.verizon.com/about/news/example",
                        "description": "secret description",
                    }
                ],
                "expected_changes": [],
                "expected_alert": False,
                "why": "secret why",
            }
        ],
    }
    path = tmp_path / "bad_filed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReplayValidationError, match="filed") as excinfo:
        load_replay_events(session, path)
    assert type(excinfo.value) is ReplayValidationError


def test_real_replay_seed_loads_clean_summaries(session: Session) -> None:
    load_seed(session, SEED_PATH)
    session.commit()
    rows = load_replay_events(session, DEFAULT_REPLAY_PATH)
    session.commit()
    assert len(rows) == 29
    assert session.scalar(select(func.count()).select_from(ReplayExpectation)) == 29

    for exp in rows:
        issuer = resolve_issuer(session, exp.issuer_key)
        assert issuer is not None
        primary = (exp.sources or [None])[0]
        assert primary is not None
        filed_s = primary.get("filed") or exp.available_from.isoformat()
        summary = neutral_summary(
            issuer_name=issuer.name,
            form=str(primary.get("form") or "document"),
            filed=date.fromisoformat(str(filed_s)),
        )
        forbidden = [
            exp.key,
            exp.title,
            exp.why or "",
            exp.notes or "",
            *(str(s.get("description") or "") for s in (exp.sources or [])),
            *(str(s.get("description") or "") for s in (exp.confirming_sources or [])),
        ]
        for snippet in forbidden:
            if not snippet:
                continue
            assert snippet not in summary, f"{exp.key}: summary leaked {snippet!r}"


def test_step_stops_at_first_eventful_day(
    session: Session, verizon: Issuer, clients: ReplayClients
) -> None:
    load_replay_events(session, REPLAY_JSON)
    session.commit()
    # 11 May 2026 is a Monday — only the tender launch, no rate_update.
    first = step_to_next(
        session,
        date(2026, 6, 17),
        clients,
        window_start=date(2026, 5, 11),
        after=date(2026, 5, 10),
    )
    session.commit()
    assert {e.replay_key for e in first} == {"vz_tender_launch"}
    assert {e.event_date for e in first} == {date(2026, 5, 11)}
    assert (
        session.scalars(select(Event).where(Event.replay_key == "vz_tender_results")).first()
        is None
    )

    # Next eventful day is Friday 15 May (weekly rate_update), not the June results.
    second = step_to_next(session, date(2026, 6, 17), clients, window_start=date(2026, 5, 11))
    session.commit()
    assert second
    assert {e.event_date for e in second} == {date(2026, 5, 15)}
    assert all(e.event_type == "rate_update" for e in second)
    assert (
        session.scalars(select(Event).where(Event.replay_key == "vz_tender_results")).first()
        is None
    )


def test_extract_document_title() -> None:
    html = "<html><head><title>  Hello &amp; World  </title></head></html>"
    assert extract_document_title(html) == "Hello & World"


def test_parse_edgar_index_url() -> None:
    url = (
        "https://www.sec.gov/Archives/edgar/data/1326160/000132616026000026/"
        "0001326160-26-000026-index.htm"
    )
    assert parse_edgar_index_url(url) == ("1326160", "0001326160-26-000026")
    assert parse_edgar_index_url("https://www.verizon.com/about/news") is None


def test_select_index_documents_skips_noise_keeps_primary_and_ex99() -> None:
    docs = [
        DocumentRef(name="d8k.htm", type="8-K", url="https://example/d8k.htm"),
        DocumentRef(name="dex991.htm", type="EX-99.1", url="https://example/dex991.htm"),
        DocumentRef(name="logo.jpg", type="GRAPHIC", url="https://example/logo.jpg"),
        DocumentRef(name="R1.htm", type="XML", url="https://example/R1.htm"),
        DocumentRef(name="schema.xsd", type="EX-101.SCH", url="https://example/schema.xsd"),
        DocumentRef(name="full.txt", type="", url="https://example/full.txt"),
    ]
    selected = select_index_documents(docs)
    assert [d.type for d in selected] == ["8-K", "EX-99.1"]


def test_duke_q1_index_resolves_to_10q_body(
    session: Session, duke_corp: Issuer, edgar_client: EdgarClient
) -> None:
    clients = ReplayClients(edgar=edgar_client, newsroom=FakeFetcher({}))
    load_replay_events(session, DUKE_Q1_JSON)
    session.commit()
    created = advance_to(session, date(2026, 5, 5), clients)
    session.commit()
    event = next(e for e in created if e.replay_key == "duk_parent_tl_repaid")
    links = session.scalars(select(EventFiling).where(EventFiling.event_id == event.id)).all()
    assert len(links) == 1
    filing = session.get(Filing, links[0].filing_id)
    assert filing is not None
    assert filing.url.endswith("duk-20260331.htm")
    assert filing.form_type == "10-Q"
    assert filing.body_text
    assert "Term Loan Facilities" in filing.body_text or "term loan" in filing.body_text.lower()
    assert event.source_filing_id == filing.id


def test_shared_duke_10q_merges_into_one_parent_event(
    session: Session, duke_corp: Issuer, edgar_client: EdgarClient
) -> None:
    """Two expectations sharing one 10-Q → one event on duke_corp; both expectations linked."""
    carolinas = Issuer(
        key="duke_carolinas",
        name="Duke Energy Carolinas, LLC",
        cik="17797",
        parent_id=duke_corp.id,
    )
    session.add(carolinas)
    session.flush()
    clients = ReplayClients(edgar=edgar_client, newsroom=FakeFetcher({}))
    load_replay_events(session, DUKE_Q1_MERGED_JSON)
    session.commit()
    created = advance_to(session, date(2026, 5, 5), clients)
    session.commit()
    # One merged event (not two).
    seeded = [e for e in created if e.replay_key and not e.replay_key.startswith("rate_update")]
    assert len(seeded) == 1
    event = seeded[0]
    assert event.replay_key.startswith("filing:0001326160-26-000026:")
    assert event.issuer_id == duke_corp.id
    assert "registrants:" in (event.summary or "")
    assert "Duke Energy Corporation" in (event.summary or "")
    assert "Duke Energy Carolinas" in (event.summary or "")
    exps = session.scalars(select(ReplayExpectation).order_by(ReplayExpectation.key)).all()
    assert {e.key for e in exps} == {"dec_term_loan", "duk_parent_tl_repaid"}
    assert all(e.event_id == event.id for e in exps)

    from app.agent.loop import assemble_user_input

    payload = json.loads(assemble_user_input(session, event))
    keys = {r["key"] for r in payload["registrants"]}
    assert keys == {"duke_corp", "duke_carolinas"}


def test_verizon_8k_index_resolves_primary_and_ex99(
    session: Session, verizon: Issuer, edgar_client: EdgarClient
) -> None:
    clients = ReplayClients(edgar=edgar_client, newsroom=FakeFetcher({}))
    load_replay_events(session, VERIZON_8K_JSON)
    session.commit()
    created = advance_to(session, date(2026, 6, 17), clients)
    session.commit()
    event = next(e for e in created if e.replay_key == "vz_frontier_8k")
    links = session.scalars(select(EventFiling).where(EventFiling.event_id == event.id)).all()
    filings = [session.get(Filing, link.filing_id) for link in links]
    assert len(filings) == 2
    by_url = {f.url: f for f in filings if f is not None}
    primary = next(f for f in by_url.values() if f.url.endswith("d15574d8k.htm"))
    exhibit = next(f for f in by_url.values() if f.url.endswith("d15574dex991.htm"))
    assert primary.form_type == "8-K"
    assert primary.body_text and "Form 8-K" in primary.body_text
    assert exhibit.form_type == "press_release"
    assert exhibit.body_text and (
        "Exhibit 99.1" in exhibit.body_text or "News Release" in exhibit.body_text
    )
    assert event.source_filing_id == primary.id
