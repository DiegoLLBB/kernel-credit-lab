"""FX conversion helpers (DEXCAUS invert; DEXUSEU / DEXUSUK as USD per unit)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import RateObservation
from app.tools.fx import (
    FxRateMissingError,
    amount_to_usd,
    resolve_fx_rate,
    usd_per_unit,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_usd_per_unit_invert_dexcaus() -> None:
    assert usd_per_unit("DEXCAUS", Decimal("1.2500")) == Decimal("0.80000000")
    assert usd_per_unit("DEXUSEU", Decimal("1.1000")) == Decimal("1.1000")
    assert usd_per_unit("DEXUSUK", Decimal("1.3000")) == Decimal("1.3000")


def test_cad_125bn_at_known_rate(session: Session) -> None:
    session.add(
        RateObservation(series_id="DEXCAUS", obs_date=date(2026, 6, 11), value=Decimal("1.25"))
    )
    session.commit()
    usd = amount_to_usd(session, Decimal("1250000000"), "CAD", date(2026, 6, 12))
    assert usd == Decimal("1000000000.00")
    fx = resolve_fx_rate(session, "CAD", date(2026, 6, 12))
    assert fx is not None
    assert fx.series_id == "DEXCAUS"
    assert fx.to_dict()["inverted"] is True
    assert fx.raw_rate == Decimal("1.25")
    assert fx.obs_date == date(2026, 6, 11)


def test_missing_fx_rate_raises(session: Session) -> None:
    with pytest.raises(FxRateMissingError, match="DEXCAUS"):
        amount_to_usd(session, Decimal("100"), "CAD", date(2026, 6, 12))
