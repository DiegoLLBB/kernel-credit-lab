"""Helpers for chart_reading facts used as propose_change / submit_alert evidence."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import ChartReading, Fact


def is_chart_reading(fact: Fact) -> bool:
    return (fact.fact_type or "quote") == "chart_reading"


def chart_reading_verified(session: Session, fact: Fact) -> tuple[bool, str | None]:
    """Return (ok, problem) for a chart_reading fact."""
    if not is_chart_reading(fact):
        return False, "not a chart_reading fact"
    meta = fact.meta or {}
    chart_id = meta.get("chart_id")
    if not chart_id:
        return False, "chart_reading fact missing chart_id"
    chart = session.get(ChartReading, str(chart_id))
    if chart is None:
        return False, f"chart {chart_id!r} not found"
    if not chart.verified:
        reason = (chart.verification_detail or {}).get("reason") or "geometry check failed"
        return False, f"chart {chart_id!r} is unverified ({reason})"
    return True, None


def chart_value(fact: Fact) -> Decimal | None:
    meta = fact.meta or {}
    raw = meta.get("value")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:  # noqa: BLE001
        return None


def fact_supports_amount(
    session: Session,
    fact: Fact,
    value: Any,
    *,
    quote_check,
) -> bool:
    """True when a quote or verified chart_reading supports ``value``."""
    if is_chart_reading(fact):
        ok, _ = chart_reading_verified(session, fact)
        if not ok:
            return False
        chart_val = chart_value(fact)
        if chart_val is None:
            return False
        try:
            target = Decimal(str(value).replace(",", "").replace("$", "").strip())
        except Exception:  # noqa: BLE001
            return False
        return abs(chart_val - target) <= max(Decimal("0.01"), abs(target) * Decimal("0.001"))
    return bool(quote_check(value, fact.exact_quote))
