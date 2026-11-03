"""Pre-formatted display strings for insight facts (writer-facing, not computed in prose)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_usd_display(value: float | Decimal | None) -> str | None:
    """Prose style for model text: $30.4bn, $925M (no long decimals)."""
    if value is None:
        return None
    n = float(value)
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        bn = n / 1_000_000_000
        if abs(bn) >= 10:
            return f"${bn:,.1f}bn"
        return f"${bn:.2f}bn".replace(".00bn", "bn")
    if abs_n >= 1_000_000:
        m = n / 1_000_000
        if abs(m) >= 100:
            return f"${m:,.0f}M"
        return f"${m:.1f}M".replace(".0M", "M")
    if abs_n >= 1_000:
        return f"${n:,.0f}"
    return f"${n:,.2f}"


def format_usd_wall(value: float | Decimal | None) -> str | None:
    """Wall / feed style: always $M with thousands separators ($11,889M)."""
    if value is None:
        return None
    m = float(value) / 1_000_000
    signed = "-" if m < 0 else ""
    return f"{signed}${abs(m):,.0f}M"


def format_multiple_display(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{float(value):.1f}×"


def format_date_display(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
