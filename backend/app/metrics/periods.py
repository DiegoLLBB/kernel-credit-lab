"""Same-period year-on-year prior selection for credit metrics."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Protocol


class _PeriodRow(Protocol):
    period_end: date


_SAME_PERIOD_TOLERANCE_DAYS = 45


def anniversary(d: date) -> date:
    """Same calendar day one year earlier (clamped for 29 Feb)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return date(d.year - 1, d.month, monthrange(d.year - 1, d.month)[1])


def prior_same_period[T: _PeriodRow](series: list[T], latest: T) -> T | None:
    """Row whose ``period_end`` is the same point one year earlier.

    Prefers the closest ``period_end`` within ±45 days of the anniversary of
    ``latest.period_end``. Returns None when nothing is close enough (UI shows —).
    Never falls back to a fiscal year two years back.
    """
    if not series:
        return None
    target = anniversary(latest.period_end)
    best: T | None = None
    best_delta: int | None = None
    for row in series:
        if row is latest or row.period_end >= latest.period_end:
            continue
        delta = abs((row.period_end - target).days)
        if delta > _SAME_PERIOD_TOLERANCE_DAYS:
            continue
        if best is None or best_delta is None or delta < best_delta:
            best = row
            best_delta = delta
    return best


def prior_cutoff_legacy(latest_end: date) -> date:
    """Previous sliding-window cutoff (kept for tests that pin old behaviour)."""
    return latest_end - timedelta(days=365)
