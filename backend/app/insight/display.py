"""Pre-formatted display strings for insight facts (writer-facing, not computed in prose)."""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

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

# Ignore FX / rounding ripples under this floor when listing changed wall buckets.
MIN_WALL_CHANGE_ABS = 100_000_000.0

# Short names for natural impact/brief prose.
ISSUER_SHORT: dict[str, str] = {
    "amazon": "Amazon",
    "verizon": "Verizon",
    "oracle": "Oracle",
    "intel": "Intel",
    "boeing": "Boeing",
    "duke_corp": "Duke Energy",
    "duke_carolinas": "Duke Carolinas",
    "duke_progress": "Duke Progress",
    "duke_florida": "Duke Florida",
    "msft": "Microsoft",
    "t": "AT&T",
    "jpm": "JPMorgan",
    "cat": "Caterpillar",
    "jnj": "Johnson & Johnson",
    "nee": "NextEra Energy",
    "pg": "P&G",
    "gm": "GM",
    "wmt": "Walmart",
    "xom": "Exxon Mobil",
}


def issuer_short_name(key: str | None, full_name: str | None = None) -> str:
    if key and key in ISSUER_SHORT:
        return ISSUER_SHORT[key]
    if full_name:
        # "Amazon.com, Inc." → "Amazon"; "Verizon Communications Inc." → "Verizon"
        head = full_name.split(",")[0].strip()
        for noise in (" Communications Inc.", " Corporation", " Company", " Inc.", " Corp."):
            if head.endswith(noise):
                head = head[: -len(noise)].strip()
        return head or full_name
    return key or "This issuer"


def round_sig_half_up(value: float | Decimal, sig: int = 3) -> Decimal:
    """Round half-up to ``sig`` significant figures (11.25 → 11.3)."""
    d = Decimal(str(value))
    if d == 0:
        return Decimal(0)
    sign = Decimal(1) if d > 0 else Decimal(-1)
    abs_d = abs(d)
    order = int(math.floor(math.log10(float(abs_d))))
    quant_exp = order - sig + 1
    quant = Decimal(1).scaleb(quant_exp)
    return sign * abs_d.quantize(quant, rounding=ROUND_HALF_UP)


def _format_sig_number(rounded: Decimal, sig: int = 3) -> str:
    """Render a sig-fig-rounded Decimal keeping significant decimals (13.0, 30.4, 106)."""
    as_float = float(rounded)
    if as_float == 0:
        return "0"
    order = int(math.floor(math.log10(abs(as_float))))
    decimals = max(0, sig - 1 - order)
    if decimals == 0:
        return f"{int(round(as_float)):,}"
    return f"{as_float:,.{decimals}f}"


def format_usd_display(value: float | Decimal | None) -> str | None:
    """Prose style: $30.4bn / $11.3bn / $925M — half-up to 3 significant figures for $bn."""
    if value is None:
        return None
    n = float(value)
    abs_n = abs(n)
    signed = "-" if n < 0 else ""
    if abs_n >= 1_000_000_000:
        bn = round_sig_half_up(abs_n / 1_000_000_000, 3)
        return f"{signed}${_format_sig_number(bn, 3)}bn"
    if abs_n >= 1_000_000:
        m = abs_n / 1_000_000
        if abs(m) >= 100:
            return f"{signed}${round(m):,.0f}M"
        rounded_m = round_sig_half_up(m, 3)
        return f"{signed}${_format_sig_number(rounded_m, 3)}M"
    if abs_n >= 1_000:
        return f"{signed}${abs_n:,.0f}"
    return f"{signed}${abs_n:,.2f}"


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
    rounded = round_sig_half_up(float(value), 3)
    return f"{_format_sig_number(rounded)}×"


def format_date_display(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def format_duration_ms(ms: int | float | None) -> str:
    """428536 → '7 min 9 s'."""
    if ms is None:
        return "—"
    total = max(0, int(round(float(ms) / 1000)))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes or hours:
        parts.append(f"{minutes} min")
    parts.append(f"{seconds} s")
    return " ".join(parts)


def bucket_year_label(bucket: str | None) -> str:
    """FY2029 → '2029'; after FY2030 → 'after 2030'."""
    if not bucket:
        return "the maturity wall"
    text = bucket.strip()
    if text.lower().startswith("after "):
        rest = text[6:].strip()
        if rest.upper().startswith("FY") and rest[2:].isdigit():
            return f"after {rest[2:]}"
        return text.lower()
    if text.upper().startswith("FY") and text[2:].isdigit():
        return text[2:]
    return text


def bucket_sort_key(bucket: str | None) -> tuple[int, int]:
    """Order FY2026–FY2029 (and FY2030) before 'after FY2030'."""
    if not bucket:
        return (2, 9999)
    text = bucket.strip()
    lower = text.lower()
    if lower.startswith("after"):
        year = 2030
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            year = int(digits[:4])
        return (1, year)
    digits = "".join(ch for ch in text if ch.isdigit())
    year = int(digits[:4]) if digits else 9999
    return (0, year)
