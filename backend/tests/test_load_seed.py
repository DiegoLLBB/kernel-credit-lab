"""Seed loader: valid snapshot loads, re-load is idempotent, bad files are refused."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    CallTerms,
    CuratedSource,
    Instrument,
    Issuer,
    MaturitySchedule,
    Watchlist,
)
from app.tools.maturity import bucket_for
from scripts.load_seed import SeedValidationError, load_seed, validate_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def snapshot() -> dict:
    with SEED_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_valid_seed_loads(session: Session, snapshot: dict) -> None:
    n_issuers = len(snapshot["issuers"])
    n_instruments = len(snapshot["instruments"])
    n_schedules = len(snapshot["maturity_schedules"])
    n_watchlist = len(snapshot["watchlist"])

    stats = load_seed(session, SEED_PATH)
    session.commit()

    assert stats.issuers_created == n_issuers
    assert stats.instruments_created == n_instruments
    assert stats.maturity_schedules_created == n_schedules
    assert session.scalar(select(func.count()).select_from(Issuer)) == n_issuers
    assert session.scalar(select(func.count()).select_from(Instrument)) == n_instruments
    assert session.scalar(select(func.count()).select_from(Watchlist)) == n_watchlist
    assert session.scalar(select(func.count()).select_from(CallTerms)) >= 1
    assert session.scalar(select(func.count()).select_from(CuratedSource)) > 0
    assert session.scalar(select(func.count()).select_from(MaturitySchedule)) == n_schedules

    opco = session.scalars(select(Issuer).where(Issuer.cik == "30371")).one()
    holdco = session.scalars(select(Issuer).where(Issuer.cik == "1326160")).one()
    assert opco.parent_id == holdco.id
    assert holdco.ticker == "DUK"

    loan = session.scalars(select(Instrument).where(Instrument.key == "duk_tl_2026")).one()
    assert loan.rate_type == "floating"
    assert loan.instrument_type == "loan"
    assert loan.cusip is None

    bond = session.scalars(select(Instrument).where(Instrument.cusip == "68389XCD5")).one()
    assert bond.call_terms is not None
    assert bond.call_terms.make_whole_spread_bps == 15

    coupon_src = session.scalars(
        select(CuratedSource).where(
            CuratedSource.instrument_id == bond.id, CuratedSource.field == "coupon"
        )
    ).one()
    assert coupon_src.value == "2.300"
    assert coupon_src.source_url
    assert "sec.gov" in coupon_src.source_url
    assert coupon_src.as_of is not None

    duk_sched = session.scalars(
        select(MaturitySchedule).where(MaturitySchedule.issuer_id == holdco.id)
    ).one()
    assert duk_sched.basis == "consolidated"
    assert duk_sched.as_of.isoformat() == "2025-12-31"
    assert duk_sched.source_url
    assert duk_sched.buckets[0]["label"] == "FY2026"
    assert Decimal(str(duk_sched.buckets[0]["amount"])) > 0


def test_second_load_changes_nothing(session: Session, snapshot: dict) -> None:
    n_issuers = len(snapshot["issuers"])
    n_instruments = len(snapshot["instruments"])
    n_schedules = len(snapshot["maturity_schedules"])

    load_seed(session, SEED_PATH)
    session.commit()

    issuer_ids = {i.cik: i.id for i in session.scalars(select(Issuer)).all() if i.cik is not None}
    instrument_ids = {
        i.key: i.id for i in session.scalars(select(Instrument)).all() if i.key is not None
    }
    schedule_ids = {
        (s.issuer_id, s.as_of): s.id for s in session.scalars(select(MaturitySchedule)).all()
    }
    n_db_issuers = session.scalar(select(func.count()).select_from(Issuer))
    n_db_instruments = session.scalar(select(func.count()).select_from(Instrument))
    n_watchlist = session.scalar(select(func.count()).select_from(Watchlist))
    n_call_terms = session.scalar(select(func.count()).select_from(CallTerms))
    n_sources = session.scalar(select(func.count()).select_from(CuratedSource))
    n_db_schedules = session.scalar(select(func.count()).select_from(MaturitySchedule))
    amounts = {
        i.key: i.amount_outstanding
        for i in session.scalars(select(Instrument)).all()
        if i.key is not None
    }

    stats = load_seed(session, SEED_PATH)
    session.commit()

    assert stats.issuers_created == 0
    assert stats.instruments_created == 0
    assert stats.watchlist_created == 0
    assert stats.maturity_schedules_created == 0
    assert stats.issuers_updated == n_issuers
    assert stats.instruments_updated == n_instruments
    assert stats.maturity_schedules_updated == n_schedules

    assert session.scalar(select(func.count()).select_from(Issuer)) == n_db_issuers
    assert session.scalar(select(func.count()).select_from(Instrument)) == n_db_instruments
    assert session.scalar(select(func.count()).select_from(Watchlist)) == n_watchlist
    assert session.scalar(select(func.count()).select_from(CallTerms)) == n_call_terms
    assert session.scalar(select(func.count()).select_from(CuratedSource)) == n_sources
    assert session.scalar(select(func.count()).select_from(MaturitySchedule)) == n_db_schedules
    assert {
        i.cik: i.id for i in session.scalars(select(Issuer)).all() if i.cik is not None
    } == issuer_ids
    assert {
        i.key: i.id for i in session.scalars(select(Instrument)).all() if i.key is not None
    } == instrument_ids
    assert {
        (s.issuer_id, s.as_of): s.id for s in session.scalars(select(MaturitySchedule)).all()
    } == schedule_ids
    assert {
        i.key: i.amount_outstanding
        for i in session.scalars(select(Instrument)).all()
        if i.key is not None
    } == amounts


def test_bad_seed_is_rejected_with_clear_message(
    session: Session, snapshot: dict, tmp_path: Path
) -> None:
    before_instruments = session.scalar(select(func.count()).select_from(Instrument)) or 0

    bad = copy.deepcopy(snapshot)
    bad["instruments"][0]["amount_outstanding"] = "0"
    bad["instruments"][1]["issue_date"] = "2030-01-01"  # after maturity
    bad["instruments"][2]["issuer"] = "missing_issuer"
    bad["instruments"][3]["sources"] = ["no_such_source"]
    bad["instruments"][4]["rate_type"] = "fixed"
    bad["instruments"][4]["reference_rate"] = "SOFR"
    bad["instruments"][4]["spread_bps"] = 100
    bad["maturity_schedules"][0]["buckets"][0]["amount"] = "-1"
    bad["maturity_schedules"][1]["issuer"] = "ghost"
    bad["maturity_schedules"][2]["source"] = "missing_src"

    with pytest.raises(SeedValidationError) as raised:
        validate_seed(bad)
    message = str(raised.value)
    assert "Seed validation failed" in message
    assert "amount_outstanding must be > 0" in message
    assert "must be before maturity" in message
    assert "not in issuers" in message
    assert "unknown source" in message
    assert "floating-rate instruments must be flagged" in message
    assert "amount must be >= 0" in message
    assert "ghost" in message
    assert "missing_src" in message

    bad_path = tmp_path / "bad_snapshot.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SeedValidationError) as load_raised:
        load_seed(session, bad_path)
    session.rollback()
    assert "amount_outstanding must be > 0" in str(load_raised.value)
    assert (session.scalar(select(func.count()).select_from(Instrument)) or 0) == before_instruments


def test_active_instruments_never_exceed_schedule_by_year(session: Session) -> None:
    """Active instruments assigned via bucket_for must fit each schedule bucket.

    Consolidated schedules (e.g. Duke holdco) are checked against the issuer plus
    its subsidiaries' instruments.
    """
    load_seed(session, SEED_PATH)
    session.commit()

    issuers = list(session.scalars(select(Issuer)).all())
    children: dict[str, list[str]] = defaultdict(list)
    for issuer in issuers:
        if issuer.parent_id:
            children[issuer.parent_id].append(issuer.id)

    active = list(session.scalars(select(Instrument).where(Instrument.status == "active")).all())

    for sched in session.scalars(select(MaturitySchedule)).all():
        issuer_ids = {sched.issuer_id}
        if sched.basis == "consolidated":
            issuer_ids.update(children.get(sched.issuer_id, []))

        by_label: dict[str, Decimal] = defaultdict(Decimal)
        for inst in active:
            if inst.issuer_id not in issuer_ids:
                continue
            label = bucket_for(inst.maturity, sched.buckets)
            if label is not None:
                by_label[label] += Decimal(inst.amount_outstanding)

        for bucket in sched.buckets:
            if not bucket.get("period_end"):
                continue
            label = str(bucket["label"])
            schedule_amount = Decimal(str(bucket["amount"]))
            instrument_sum = by_label[label]
            assert instrument_sum <= schedule_amount, (
                f"issuer {sched.issuer_id} basis={sched.basis} bucket {label}: "
                f"instruments {instrument_sum} exceed schedule {schedule_amount}"
            )


def test_reload_never_deletes_unrelated_rows(session: Session) -> None:
    """Reloading on a database that already holds the seed must not delete rows.

    Simulates the cloud database (seed plus replay-produced rows): an extra issuer,
    instrument and watchlist entry survive a second load untouched, and seed
    counts are unchanged.
    """
    from datetime import date

    load_seed(session, SEED_PATH)
    session.commit()
    n_instruments = session.scalar(select(func.count()).select_from(Instrument))
    n_issuers = session.scalar(select(func.count()).select_from(Issuer))

    extra_issuer = Issuer(key="extra_corp", name="Extra Corp", cik="9999999999")
    session.add(extra_issuer)
    session.flush()
    extra_bond = Instrument(
        key="extra_bond_2030",
        issuer_id=extra_issuer.id,
        name="5.000% Notes due 2030",
        coupon=Decimal("5.0"),
        maturity=date(2030, 6, 1),
        amount_outstanding=Decimal("100000000"),
    )
    session.add(extra_bond)
    session.add(Watchlist(issuer_id=extra_issuer.id, label="extra"))
    session.commit()

    stats = load_seed(session, SEED_PATH)
    session.commit()

    assert stats.issuers_created == 0
    assert stats.instruments_created == 0
    assert session.scalar(select(func.count()).select_from(Issuer)) == n_issuers + 1
    assert session.scalar(select(func.count()).select_from(Instrument)) == n_instruments + 1
    kept = session.scalars(select(Instrument).where(Instrument.key == "extra_bond_2030")).one()
    assert kept.amount_outstanding == Decimal("100000000")
    assert kept.issuer_id == extra_issuer.id
    assert (
        session.scalars(select(Watchlist).where(Watchlist.issuer_id == extra_issuer.id)).one().label
        == "extra"
    )
