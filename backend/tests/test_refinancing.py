"""Unit tests for display-only refinancing spread / tenor / coverage nulls."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import CreditMetric, Issuer, RateObservation
from app.tools.refinancing import (
    FOOTNOTE,
    SPREAD_CAP_PCT,
    SPREAD_FLOOR_PCT,
    calculate_proforma_coverage,
    calculate_rate_reset_cost,
    clamp_shock_bps,
    issuer_spread,
    normalize_tenor_years,
    refinancing_panel,
)
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"
AS_OF = date(2025, 12, 31)


@pytest.fixture()
def seeded_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        load_seed(db, SEED_PATH)
        for series, value in (
            ("DGS2", Decimal("3.90")),
            ("DGS5", Decimal("4.12")),
            ("DGS7", Decimal("4.25")),
            ("DGS10", Decimal("4.40")),
        ):
            db.add(RateObservation(series_id=series, obs_date=date(2025, 10, 30), value=value))
        db.commit()
        yield db


def _add_cod(session: Session, issuer_id: str, *, interest: str, debt: str) -> None:
    # filed ≤ AS_OF so point-in-time filters see the rows.
    session.add(
        CreditMetric(
            issuer_id=issuer_id,
            metric="interest_coverage",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            value=Decimal("4.0"),
            unit="x",
            filed=date(2025, 12, 31),
            detail={"interest_expense": interest, "ebitda": "4000000000"},
        )
    )
    session.add(
        CreditMetric(
            issuer_id=issuer_id,
            metric="total_debt",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            value=Decimal(debt),
            unit="USD",
            filed=date(2025, 12, 31),
            detail={},
        )
    )
    session.add(
        CreditMetric(
            issuer_id=issuer_id,
            metric="total_debt",
            period_end=date(2024, 12, 31),
            fiscal_label="FY2024",
            value=Decimal(debt),
            unit="USD",
            filed=date(2025, 2, 1),
            detail={},
        )
    )
    session.add(
        CreditMetric(
            issuer_id=issuer_id,
            metric="ebitda",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            value=Decimal("4000000000"),
            unit="USD",
            filed=date(2025, 12, 31),
            detail={},
        )
    )
    session.commit()


def test_normalize_tenor_and_shock() -> None:
    assert normalize_tenor_years(5) == 5
    assert normalize_tenor_years(7) == 7
    assert normalize_tenor_years(3) == 5
    assert clamp_shock_bps(400) == 300


def test_spread_floor_cap_and_derivation(seeded_session: Session) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.key == "duke_corp")).one()
    # COD 5.07% − DGS5 4.12% = 0.95% (within band)
    _add_cod(seeded_session, duke.id, interest="5070000000", debt="100000000000")
    info = issuer_spread(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert info["missing"] is None
    assert Decimal(info["spread_pct"]) == Decimal("0.95")
    assert "5-year Treasury 4.12% on 30 Oct 2025" in info["derivation_line"]
    assert "0.95% spread from 2025 cost of debt" in info["derivation_line"]

    # Tiny spread floors at 0.5%
    for row in seeded_session.scalars(
        select(CreditMetric).where(CreditMetric.issuer_id == duke.id)
    ).all():
        seeded_session.delete(row)
    seeded_session.commit()
    _add_cod(seeded_session, duke.id, interest="4200000000", debt="100000000000")
    floored = issuer_spread(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert Decimal(floored["spread_pct"]) == SPREAD_FLOOR_PCT
    assert floored["floored"] is True

    for row in seeded_session.scalars(
        select(CreditMetric).where(CreditMetric.issuer_id == duke.id)
    ).all():
        seeded_session.delete(row)
    seeded_session.commit()
    _add_cod(seeded_session, duke.id, interest="9000000000", debt="100000000000")
    capped = issuer_spread(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert Decimal(capped["spread_pct"]) == SPREAD_CAP_PCT
    assert capped["capped"] is True


def test_missing_interest_never_estimates_cod(seeded_session: Session) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.key == "duke_corp")).one()
    info = issuer_spread(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert info["spread_pct"] is None
    assert info["missing"] == "interest expense unavailable"
    reset = calculate_rate_reset_cost(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert reset["total_incremental_annual_interest"] is None
    assert reset["missing"] == "interest expense unavailable"


def test_coverage_null_without_ebitda(seeded_session: Session) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.key == "duke_corp")).one()
    _add_cod(seeded_session, duke.id, interest="5070000000", debt="100000000000")
    # Remove ebitda only.
    for row in seeded_session.scalars(
        select(CreditMetric).where(
            CreditMetric.issuer_id == duke.id, CreditMetric.metric == "ebitda"
        )
    ).all():
        seeded_session.delete(row)
    seeded_session.commit()
    cov = calculate_proforma_coverage(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert cov["coverage_before"] is None
    assert cov["missing"] == "EBITDA unavailable"


def test_bucket_coupon_source_and_panel(seeded_session: Session) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.key == "duke_corp")).one()
    _add_cod(seeded_session, duke.id, interest="5070000000", debt="100000000000")
    reset = calculate_rate_reset_cost(seeded_session, duke.id, AS_OF, tenor_years=5)
    assert reset["buckets"]
    named_bucket = next(b for b in reset["buckets"] if Decimal(b["named_amount"]) > 0)
    assert "named" in named_bucket["maturing_coupon_source"]
    panel = refinancing_panel(seeded_session, duke.id, AS_OF, shock_bps=100, tenor_years=5)
    assert panel["footnote"] == FOOTNOTE
    assert panel["base"]["derivation_line"]
    assert Decimal(panel["shocked"]["total_incremental_annual_interest"]) > Decimal(
        panel["base"]["total_incremental_annual_interest"]
    )
