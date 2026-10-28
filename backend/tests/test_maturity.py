"""Unit tests for fiscal-year maturity bucket assignment and maturity_wall."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Instrument, Issuer
from app.tools.maturity import NonUsdAmountError, bucket_for, maturity_wall
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"

# Oracle FY ends 31 May.
ORACLE_BUCKETS = [
    {"label": "FY2026", "period_end": "2026-05-31", "amount": "7309000000"},
    {"label": "FY2027", "period_end": "2027-05-31", "amount": "5743000000"},
    {"label": "FY2028", "period_end": "2028-05-31", "amount": "10145000000"},
    {"label": "FY2029", "period_end": "2029-05-31", "amount": "2000000000"},
    {"label": "FY2030", "period_end": "2030-05-31", "amount": "7250000000"},
    {"label": "after FY2030", "period_end": None, "amount": "60500000000"},
]

# Intel FY ends late December (27 Dec).
INTEL_BUCKETS = [
    {"label": "FY2026", "period_end": "2026-12-27", "amount": "2500000000"},
    {"label": "FY2027", "period_end": "2027-12-27", "amount": "3826000000"},
    {"label": "FY2028", "period_end": "2028-12-27", "amount": "3173000000"},
    {"label": "FY2029", "period_end": "2029-12-27", "amount": "3288000000"},
    {"label": "FY2030", "period_end": "2030-12-27", "amount": "2750000000"},
    {"label": "after FY2030", "period_end": None, "amount": "31698000000"},
]


def test_oracle_may_fiscal_year_boundaries() -> None:
    # Still in FY2028 (ends 31 May 2028).
    assert bucket_for(date(2028, 3, 25), ORACLE_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 5, 31), ORACLE_BUCKETS) == "FY2028"
    # Day after FY2028 end → FY2029.
    assert bucket_for(date(2028, 6, 1), ORACLE_BUCKETS) == "FY2029"
    assert bucket_for(date(2028, 8, 15), ORACLE_BUCKETS) == "FY2029"
    assert bucket_for(date(2029, 5, 31), ORACLE_BUCKETS) == "FY2029"


def test_intel_late_december_year_end() -> None:
    assert bucket_for(date(2028, 12, 15), INTEL_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 12, 27), INTEL_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 12, 28), INTEL_BUCKETS) == "FY2029"
    assert bucket_for(date(2029, 1, 1), INTEL_BUCKETS) == "FY2029"
    assert bucket_for(date(2030, 12, 27), INTEL_BUCKETS) == "FY2030"
    assert bucket_for(date(2030, 12, 28), INTEL_BUCKETS) == "after FY2030"
    assert bucket_for(date(2035, 6, 1), INTEL_BUCKETS) == "after FY2030"


def test_bucket_for_empty_and_before_first() -> None:
    assert bucket_for(date(2028, 1, 1), []) is None
    # Open on the left: anything on or before the first period_end is FY2026.
    assert bucket_for(date(2020, 1, 1), ORACLE_BUCKETS) == "FY2026"


def test_after_bucket_absent_returns_none() -> None:
    dated_only = [b for b in INTEL_BUCKETS if b["period_end"] is not None]
    assert bucket_for(date(2031, 1, 1), dated_only) is None


@pytest.fixture()
def seeded_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        load_seed(db, SEED_PATH)
        db.commit()
        yield db


def test_maturity_wall_duke_corp_fy2026_schedule_amount(seeded_session: Session) -> None:
    duke = seeded_session.scalars(
        select(Issuer).where(Issuer.cik == "1326160")  # Duke Energy Corporation
    ).one()
    wall = maturity_wall(seeded_session, duke.id, date(2025, 12, 31))

    assert wall["basis"] == "consolidated"
    assert wall["schedule_as_of"] == date(2025, 12, 31)
    fy2026 = wall["buckets"]["FY2026"]
    assert fy2026["schedule_amount"] == Decimal("7123000000")  # $7,123M
    assert fy2026["named_active_sum"] > 0
    assert fy2026["unattributed"] == Decimal("1290533000")  # ~$1,290M
    assert fy2026["breach"] is False
    assert fy2026["total_outstanding"] == (
        fy2026["named_active_sum"] + fy2026["new_since_schedule_sum"] + fy2026["unattributed"]
    )
    keys = {row["key"] for row in fy2026["named_active"]}
    assert "dec_fmb_2950_2026" in keys
    assert all(row["currency"] == "USD" for row in fy2026["named_active"])
    assert all(row["maturity"] > date(2025, 12, 31) for row in fy2026["named_active"])


def test_maturity_wall_duke_carolinas_fy2028_named(seeded_session: Session) -> None:
    carolinas = seeded_session.scalars(
        select(Issuer).where(Issuer.cik == "30371")  # Duke Energy Carolinas
    ).one()
    wall = maturity_wall(seeded_session, carolinas.id, date(2025, 12, 31))

    assert wall["basis"] == "registrant"
    fy2028 = wall["buckets"]["FY2028"]
    assert fy2028["named_active_sum"] == Decimal("950000000")  # $950M
    assert fy2028["schedule_amount"] == Decimal("1018000000")
    assert fy2028["unattributed"] == Decimal("68000000")
    assert fy2028["breach"] is False
    assert {row["key"] for row in fy2028["named_active"]} == {
        "dec_fmb_3950_2028",
        "dec_6000_2028",
    }


def test_maturity_wall_unattributed_stable_after_named_matures(
    seeded_session: Session,
) -> None:
    """Remainder is fixed at schedule as_of; maturing a named bond must not inflate it."""
    duke = seeded_session.scalars(select(Issuer).where(Issuer.cik == "1326160")).one()
    before = maturity_wall(seeded_session, duke.id, date(2025, 12, 31))
    unattributed = before["buckets"]["FY2026"]["unattributed"]
    assert unattributed == Decimal("1290533000")  # $1,290M

    conv = seeded_session.scalars(
        select(Instrument).where(Instrument.key == "duk_conv_4125_2026")
    ).one()
    conv.status = "matured"
    seeded_session.commit()

    after = maturity_wall(seeded_session, duke.id, date(2026, 6, 30))
    fy2026 = after["buckets"]["FY2026"]
    assert fy2026["unattributed"] == unattributed
    assert "duk_conv_4125_2026" not in {r["key"] for r in fy2026["named_active"]}
    assert "duk_conv_4125_2026" in {r["key"] for r in fy2026["repaid_since_schedule"]}
    assert fy2026["breach"] is False
    assert fy2026["named_active_sum"] == before["buckets"]["FY2026"]["named_active_sum"] - Decimal(
        "1725000000"
    )


def test_maturity_wall_new_issuance_adds_to_total_without_breach(
    seeded_session: Session,
) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.cik == "1326160")).one()
    before = maturity_wall(seeded_session, duke.id, date(2025, 12, 31))
    fy2028_before = before["buckets"]["FY2028"]

    seeded_session.add(
        Instrument(
            key="duk_new_2028_post_schedule",
            issuer_id=duke.id,
            name="5.000% Senior Notes due 2028 (new)",
            instrument_type="bond",
            coupon=Decimal("5.000"),
            rate_type="fixed",
            maturity=date(2028, 6, 15),
            issue_date=date(2026, 3, 1),
            amount_outstanding=Decimal("500000000"),
            currency="USD",
            status="active",
        )
    )
    seeded_session.commit()

    after = maturity_wall(seeded_session, duke.id, date(2026, 6, 30))
    fy2028 = after["buckets"]["FY2028"]
    assert fy2028["unattributed"] == fy2028_before["unattributed"]
    assert fy2028["breach"] is False
    assert fy2028["new_since_schedule_sum"] == Decimal("500000000")
    assert "duk_new_2028_post_schedule" in {r["key"] for r in fy2028["new_since_schedule"]}
    assert fy2028["total_outstanding"] == (
        fy2028["named_active_sum"] + fy2028["new_since_schedule_sum"] + fy2028["unattributed"]
    )
    assert fy2028["total_outstanding"] == fy2028_before["total_outstanding"] + Decimal("500000000")


def test_maturity_wall_non_usd_raises(seeded_session: Session) -> None:
    duke = seeded_session.scalars(select(Issuer).where(Issuer.cik == "1326160")).one()
    bond = seeded_session.scalars(select(Instrument).where(Instrument.key == "duk_2650_2026")).one()
    bond.currency = "EUR"
    seeded_session.commit()

    with pytest.raises(NonUsdAmountError, match="non-USD"):
        maturity_wall(seeded_session, duke.id, date(2025, 12, 31))
