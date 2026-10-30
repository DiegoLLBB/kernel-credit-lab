"""FX conversion for non-USD instrument amounts via FRED ``RateObservation`` rows.

Quotation conventions (FRED):
- ``DEXUSEU`` — USD per EUR
- ``DEXUSUK`` — USD per GBP
- ``DEXCAUS`` — **CAD per USD** (invert to get USD)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RateObservation

# ISO currency → FRED series. DEXCAUS is quoted inverted vs the others.
CURRENCY_SERIES: dict[str, str] = {
    "EUR": "DEXUSEU",
    "GBP": "DEXUSUK",
    "CAD": "DEXCAUS",
}
# Series that are foreign-units-per-USD (must invert to convert to USD).
INVERT_SERIES = frozenset({"DEXCAUS"})


@dataclass(frozen=True)
class FxRateUsed:
    currency: str
    series_id: str
    obs_date: date
    raw_rate: Decimal
    usd_per_unit: Decimal  # multiply foreign amount by this to get USD

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "series_id": self.series_id,
            "obs_date": self.obs_date.isoformat(),
            "raw_rate": str(self.raw_rate),
            "usd_per_unit": str(self.usd_per_unit),
            "inverted": self.series_id in INVERT_SERIES,
        }


class FxRateMissingError(ValueError):
    """No RateObservation on or before the as-of date for the needed FX series."""


def latest_on_or_before(
    session: Session, series_id: str, as_of: date
) -> tuple[date, Decimal] | None:
    """Last non-null ``RateObservation`` with ``obs_date ≤ as_of`` (DB only, no FRED API)."""
    series_id = series_id.strip().upper()
    rows = session.scalars(
        select(RateObservation)
        .where(
            RateObservation.series_id == series_id,
            RateObservation.obs_date <= as_of,
            RateObservation.value.is_not(None),
        )
        .order_by(RateObservation.obs_date.desc())
        .limit(1)
    ).all()
    if not rows:
        return None
    row = rows[0]
    if row.value is None:
        return None
    return row.obs_date, Decimal(str(row.value))


def usd_per_unit(series_id: str, raw_rate: Decimal) -> Decimal:
    """Convert a FRED FX observation into a multiply-to-get-USD factor."""
    series_id = series_id.strip().upper()
    rate = Decimal(str(raw_rate))
    if rate <= 0:
        raise ValueError(f"FX rate for {series_id} must be positive, got {rate}")
    if series_id in INVERT_SERIES:
        return (Decimal(1) / rate).quantize(Decimal("0.00000001"))
    return rate


def resolve_fx_rate(session: Session, currency: str, as_of: date) -> FxRateUsed | None:
    """Return the FX rate for ``currency`` as of ``as_of``, or None for USD / unknown."""
    cur = (currency or "USD").strip().upper()
    if cur in {"", "USD"}:
        return None
    series = CURRENCY_SERIES.get(cur)
    if series is None:
        raise FxRateMissingError(f"no FX series mapped for currency {cur!r}")
    got = latest_on_or_before(session, series, as_of)
    if got is None:
        raise FxRateMissingError(
            f"no {series} RateObservation on or before {as_of.isoformat()} for {cur}"
        )
    obs_date, raw = got
    return FxRateUsed(
        currency=cur,
        series_id=series,
        obs_date=obs_date,
        raw_rate=raw,
        usd_per_unit=usd_per_unit(series, raw),
    )


def amount_to_usd(
    session: Session,
    amount: Decimal,
    currency: str,
    as_of: date,
    *,
    rates_used: dict[str, dict[str, Any]] | None = None,
) -> Decimal:
    """Convert ``amount`` in ``currency`` to USD as of ``as_of``.

    When ``rates_used`` is provided, records the rate under the currency key.
    """
    cur = (currency or "USD").strip().upper()
    amt = Decimal(str(amount))
    if cur in {"", "USD"}:
        return amt
    fx = resolve_fx_rate(session, cur, as_of)
    assert fx is not None
    if rates_used is not None:
        rates_used[cur] = fx.to_dict()
    return (amt * fx.usd_per_unit).quantize(Decimal("0.01"))
