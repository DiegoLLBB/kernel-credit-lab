"""Seed loader: valid snapshot loads, re-load is idempotent, bad files are refused."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import CallTerms, CuratedSource, Instrument, Issuer, Watchlist
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


def test_valid_seed_loads(session: Session) -> None:
    stats = load_seed(session, SEED_PATH)
    session.commit()

    assert stats.issuers_created == 9
    assert stats.instruments_created == 22
    assert session.scalar(select(func.count()).select_from(Issuer)) == 9
    assert session.scalar(select(func.count()).select_from(Instrument)) == 22
    assert session.scalar(select(func.count()).select_from(Watchlist)) == 9
    assert session.scalar(select(func.count()).select_from(CallTerms)) >= 1
    assert session.scalar(select(func.count()).select_from(CuratedSource)) > 0

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


def test_second_load_changes_nothing(session: Session) -> None:
    load_seed(session, SEED_PATH)
    session.commit()

    issuer_ids = {i.cik: i.id for i in session.scalars(select(Issuer)).all() if i.cik is not None}
    instrument_ids = {
        i.key: i.id for i in session.scalars(select(Instrument)).all() if i.key is not None
    }
    n_issuers = session.scalar(select(func.count()).select_from(Issuer))
    n_instruments = session.scalar(select(func.count()).select_from(Instrument))
    n_watchlist = session.scalar(select(func.count()).select_from(Watchlist))
    n_call_terms = session.scalar(select(func.count()).select_from(CallTerms))
    n_sources = session.scalar(select(func.count()).select_from(CuratedSource))
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
    assert stats.issuers_updated == 9
    assert stats.instruments_updated == 22

    assert session.scalar(select(func.count()).select_from(Issuer)) == n_issuers
    assert session.scalar(select(func.count()).select_from(Instrument)) == n_instruments
    assert session.scalar(select(func.count()).select_from(Watchlist)) == n_watchlist
    assert session.scalar(select(func.count()).select_from(CallTerms)) == n_call_terms
    assert session.scalar(select(func.count()).select_from(CuratedSource)) == n_sources
    assert {
        i.cik: i.id for i in session.scalars(select(Issuer)).all() if i.cik is not None
    } == issuer_ids
    assert {
        i.key: i.id for i in session.scalars(select(Instrument)).all() if i.key is not None
    } == instrument_ids
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

    with pytest.raises(SeedValidationError) as raised:
        validate_seed(bad)
    message = str(raised.value)
    assert "Seed validation failed" in message
    assert "amount_outstanding must be > 0" in message
    assert "must be before maturity" in message
    assert "not in issuers" in message
    assert "unknown source" in message
    assert "floating-rate instruments must be flagged" in message

    bad_path = tmp_path / "bad_snapshot.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SeedValidationError) as load_raised:
        load_seed(session, bad_path)
    session.rollback()
    assert "amount_outstanding must be > 0" in str(load_raised.value)
    assert (session.scalar(select(func.count()).select_from(Instrument)) or 0) == before_instruments
