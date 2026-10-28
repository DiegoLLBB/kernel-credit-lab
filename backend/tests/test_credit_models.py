"""Create-and-query coverage for each credit-domain ORM model (SQLite in-memory)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    Alert,
    CallTerms,
    CuratedSource,
    Event,
    Filing,
    Instrument,
    Issuer,
    MetricSnapshot,
    ResearchRun,
    StateChange,
    Watchlist,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_issuer_parent_child_and_cik(session: Session) -> None:
    holdco = Issuer(name="Duke Energy Corporation", cik="1326160")
    session.add(holdco)
    session.flush()
    opco = Issuer(name="Duke Energy Carolinas, LLC", cik="30371", parent_id=holdco.id)
    session.add(opco)
    session.commit()

    found = session.scalars(select(Issuer).where(Issuer.cik == "30371")).one()
    assert found.parent_id == holdco.id
    assert found.parent.name == "Duke Energy Corporation"
    assert holdco.subsidiaries[0].name == opco.name


def test_instrument_bond_and_loan(session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()

    bond = Instrument(
        issuer_id=issuer.id,
        name="Senior unsecured 4.329% 2028",
        instrument_type="bond",
        coupon=Decimal("4.329"),
        rate_type="fixed",
        maturity=date(2028, 9, 21),
        issue_date=date(2018, 1, 1),
        amount_outstanding=Decimal("1250000000"),
        seniority="senior unsecured",
        security="unsecured",
        cusip="92343VER1",
        status="active",
    )
    loan = Instrument(
        issuer_id=issuer.id,
        name="Term loan unsecured floating",
        instrument_type="loan",
        coupon=Decimal("4.475"),
        rate_type="floating",
        maturity=date(2027, 3, 12),
        issue_date=date(2026, 3, 13),
        amount_outstanding=Decimal("1000000000"),
        seniority="unsecured",
        security="unsecured",
        cusip=None,
        status="active",
    )
    session.add_all([bond, loan])
    session.commit()

    bonds = session.scalars(select(Instrument).where(Instrument.instrument_type == "bond")).all()
    loans = session.scalars(select(Instrument).where(Instrument.instrument_type == "loan")).all()
    assert len(bonds) == 1
    assert bonds[0].cusip == "92343VER1"
    assert len(loans) == 1
    assert loans[0].cusip is None
    assert loans[0].rate_type == "floating"


def test_call_terms(session: Session) -> None:
    issuer = Issuer(name="Oracle Corporation", cik="1341439")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        issuer_id=issuer.id,
        name="Senior unsecured 2.300% 2028",
        coupon=Decimal("2.300"),
        maturity=date(2028, 3, 25),
        amount_outstanding=Decimal("2000000000"),
        cusip="68389XCD5",
    )
    session.add(instrument)
    session.flush()
    terms = CallTerms(
        instrument_id=instrument.id,
        par_call_date=date(2028, 1, 25),
        make_whole_spread_bps=15,
    )
    session.add(terms)
    session.commit()

    loaded = session.scalars(
        select(CallTerms).where(CallTerms.instrument_id == instrument.id)
    ).one()
    assert loaded.par_call_date == date(2028, 1, 25)
    assert loaded.make_whole_spread_bps == 15
    assert instrument.call_terms is loaded


def test_curated_source(session: Session) -> None:
    issuer = Issuer(name="Intel Corporation", cik="50863")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        issuer_id=issuer.id,
        name="Senior unsecured 4.875% 2028",
        coupon=Decimal("4.875"),
        maturity=date(2028, 2, 10),
        amount_outstanding=Decimal("1750000000"),
        cusip="458140CE8",
    )
    session.add(instrument)
    session.flush()
    source = CuratedSource(
        instrument_id=instrument.id,
        field="amount_outstanding",
        value="1750000000",
        source_url="https://www.sec.gov/Archives/edgar/data/50863/example.htm",
        as_of=date(2025, 12, 27),
        note="FY2025 10-K debt table",
    )
    session.add(source)
    session.commit()

    found = session.scalars(
        select(CuratedSource).where(CuratedSource.field == "amount_outstanding")
    ).one()
    assert found.value == "1750000000"
    assert found.as_of == date(2025, 12, 27)
    assert "10-K" in (found.note or "")


def test_filing(session: Session) -> None:
    issuer = Issuer(name="Amazon.com, Inc.", cik="1018724")
    session.add(issuer)
    session.flush()
    filing = Filing(
        issuer_id=issuer.id,
        form_type="10-K",
        accession_number="0001018724-26-000001",
        url="https://www.sec.gov/Archives/edgar/data/1018724/example.htm",
        filed_at=date(2026, 2, 1),
        content_hash="abc123",
        title="Amazon FY2025 Form 10-K",
    )
    session.add(filing)
    session.commit()

    found = session.scalars(
        select(Filing).where(Filing.accession_number == "0001018724-26-000001")
    ).one()
    assert found.form_type == "10-K"
    assert found.content_hash == "abc123"


def test_event(session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()
    filing = Filing(
        issuer_id=issuer.id,
        form_type="8-K",
        url="https://www.verizon.com/about/news/example",
        filed_at=date(2026, 8, 20),
        content_hash="redeems",
    )
    session.add(filing)
    session.flush()
    event = Event(
        event_type="redemption",
        issuer_id=issuer.id,
        event_date=date(2026, 9, 21),
        detected_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        source_filing_id=filing.id,
        summary="Verizon redeems $1.25bn 4.329% 2028 notes",
    )
    session.add(event)
    session.commit()

    found = session.scalars(select(Event).where(Event.event_type == "redemption")).one()
    assert found.issuer_id == issuer.id
    assert found.source_filing_id == filing.id
    assert found.event_date == date(2026, 9, 21)


def test_state_change(session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        issuer_id=issuer.id,
        name="Senior unsecured 4.329% 2028",
        coupon=Decimal("4.329"),
        maturity=date(2028, 9, 21),
        amount_outstanding=Decimal("1250000000"),
        status="active",
    )
    run = ResearchRun(question="Redemption of Verizon 4.329% 2028")
    session.add_all([instrument, run])
    session.flush()
    change = StateChange(
        instrument_id=instrument.id,
        run_id=run.id,
        proposed_diff={"status": {"from": "active", "to": "redeemed"}},
        evidence={"url": "https://www.verizon.com/about/news/example"},
        verifier_result={"ok": True},
        status="proposed",
    )
    session.add(change)
    session.commit()

    found = session.scalars(select(StateChange).where(StateChange.status == "proposed")).one()
    assert found.proposed_diff["status"]["to"] == "redeemed"
    assert found.verifier_result == {"ok": True}
    assert found.run_id == run.id


def test_alert(session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()
    event = Event(
        event_type="redemption",
        issuer_id=issuer.id,
        event_date=date(2026, 9, 21),
    )
    run = ResearchRun(question="Materiality for Verizon redemption")
    session.add_all([event, run])
    session.flush()
    alert = Alert(
        event_id=event.id,
        issuer_id=issuer.id,
        run_id=run.id,
        title="Verizon redeems $1.25bn 2028 notes",
        before_metrics={"maturities_24m": 2_500_000_000},
        after_metrics={"maturities_24m": 1_250_000_000},
        materiality_reasons=["maturities change >= $500M"],
        status="new",
    )
    session.add(alert)
    session.commit()

    found = session.scalars(select(Alert).where(Alert.status == "new")).one()
    assert found.title.startswith("Verizon")
    assert found.before_metrics["maturities_24m"] == 2_500_000_000
    assert found.after_metrics["maturities_24m"] == 1_250_000_000
    assert found.materiality_reasons == ["maturities change >= $500M"]


def test_watchlist(session: Session) -> None:
    issuer = Issuer(name="The Boeing Company", cik="12927")
    session.add(issuer)
    session.flush()
    entry = Watchlist(issuer_id=issuer.id, label="core IG")
    session.add(entry)
    session.commit()

    found = session.scalars(select(Watchlist).where(Watchlist.issuer_id == issuer.id)).one()
    assert found.label == "core IG"
    assert found.issuer.name == "The Boeing Company"


def test_metric_snapshot(session: Session) -> None:
    issuer = Issuer(name="Duke Energy Corporation", cik="1326160")
    session.add(issuer)
    session.flush()
    run = ResearchRun(question="Recompute Duke pressure")
    session.add(run)
    session.flush()
    snap = MetricSnapshot(
        issuer_id=issuer.id,
        as_of=date(2026, 6, 30),
        metrics={"rate_reset_cost": 42_000_000, "rank": 3},
        run_id=run.id,
    )
    session.add(snap)
    session.commit()

    found = session.scalars(
        select(MetricSnapshot).where(MetricSnapshot.issuer_id == issuer.id)
    ).one()
    assert found.as_of == date(2026, 6, 30)
    assert found.metrics["rank"] == 3
    assert found.run_id == run.id
