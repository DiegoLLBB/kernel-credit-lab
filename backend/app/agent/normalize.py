"""Normalisation helpers for quote, amount and date evidence checks."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Curly quotes / dashes → ASCII for quote matching (case otherwise preserved).
_QUOTE_TRANS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)
_WS_RE = re.compile(r"\s+")
# Prefer comma-grouped thousands (one or more groups) or a plain digit run — never
# let ``\d{1,3}`` alone steal a prefix of a longer ungrouped number like 9999000000.
_AMOUNT_TOKEN_RE = re.compile(
    r"(?P<sym>\$)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>billion|million|bn|mm|m(?![a-z])|b(?![a-z]))?",
    re.I,
)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%m/%d/%Y",
    "%d/%m/%Y",
)
_MONTH_DATE_RE = re.compile(
    r"\b("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{4}"
    r")\b",
    re.I,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

MIN_QUOTE_WORDS = 8
MIN_QUOTE_CHARS = 40


def normalize_for_quote_match(text: str) -> str:
    """Collapse whitespace; map curly quotes and dashes; keep case."""
    mapped = text.translate(_QUOTE_TRANS)
    return _WS_RE.sub(" ", mapped).strip()


def quote_has_context(quote: str) -> bool:
    """True when the quote shows context (not a lone number/token)."""
    text = quote.strip()
    if len(text) >= MIN_QUOTE_CHARS:
        return True
    return len(text.split()) >= MIN_QUOTE_WORDS


def find_quote_offset(haystack: str, quote: str) -> int | None:
    """Return character offset of ``quote`` in ``haystack`` after normalisation, or None."""
    norm_h = normalize_for_quote_match(haystack)
    norm_q = normalize_for_quote_match(quote)
    if not norm_q:
        return None
    idx = norm_h.find(norm_q)
    if idx < 0:
        return None
    mapped = haystack.translate(_QUOTE_TRANS)
    orig_i = 0
    norm_i = 0
    while orig_i < len(mapped) and norm_i < idx:
        if mapped[orig_i].isspace():
            while orig_i < len(mapped) and mapped[orig_i].isspace():
                orig_i += 1
            if norm_i < idx:
                norm_i += 1
            continue
        orig_i += 1
        norm_i += 1
    return orig_i


def _match_to_amount(match: re.Match[str]) -> Decimal | None:
    raw = match.group("num").replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    unit = (match.group("unit") or "").lower()
    if unit in {"billion", "bn", "b"}:
        value *= Decimal("1000000000")
    elif unit in {"million", "mm", "m"}:
        value *= Decimal("1000000")
    return value


def parse_amount(text: str) -> Decimal | None:
    """Parse the first monetary amount in ``text`` into a Decimal (USD face)."""
    match = _AMOUNT_TOKEN_RE.search(text.replace("\u00a0", " "))
    if not match:
        return None
    return _match_to_amount(match)


def extract_numbers(text: str, *, money_only: bool = False) -> list[Decimal]:
    """All parseable amounts in ``text`` (order of appearance).

    With ``money_only=True`` (alert rules), ignore bare integers like years/days.
    """
    found: list[Decimal] = []
    for match in _AMOUNT_TOKEN_RE.finditer(text.replace("\u00a0", " ")):
        value = _match_to_amount(match)
        if value is None:
            continue
        raw_num = match.group("num")
        unit = (match.group("unit") or "").lower()
        has_sym = bool(match.group("sym"))
        if money_only:
            looks_money = (
                has_sym
                or bool(unit)
                or ("," in raw_num)
                or (value >= 1000 and "." in raw_num.replace(",", ""))
                or value >= Decimal("1000000")
            )
            if not looks_money:
                continue
        found.append(value)
    return found


def coerce_amount(value: str | Decimal | int | float) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    parsed = parse_amount(str(value))
    if parsed is not None:
        return parsed
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def amount_in_quote(value: str | Decimal | int | float, quote: str) -> bool:
    """True when ``value`` equals any amount extracted from ``quote``."""
    target = coerce_amount(value)
    if target is None:
        return False
    return any(n == target for n in extract_numbers(quote))


def amounts_equal(a: str | Decimal | int | float, b: str | Decimal | int | float) -> bool:
    """True when two amount representations denote the same number."""
    # If either side looks like a multi-amount quote, prefer any-match.
    if isinstance(b, str) and len(extract_numbers(b)) > 1:
        return amount_in_quote(a, b)
    if isinstance(a, str) and len(extract_numbers(a)) > 1:
        return amount_in_quote(b, a)
    da = coerce_amount(a)
    db = coerce_amount(b)
    if da is None or db is None:
        return False
    return da == db


def _parse_one_date(chunk: str) -> date | None:
    cleaned = normalize_for_quote_match(chunk)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def extract_dates(text: str) -> list[date]:
    """All recognisable dates in ``text`` (ISO and common month-name forms)."""
    cleaned = normalize_for_quote_match(text)
    found: list[date] = []
    seen: set[date] = set()

    def add(d: date | None) -> None:
        if d is not None and d not in seen:
            seen.add(d)
            found.append(d)

    for match in _ISO_DATE_RE.finditer(cleaned):
        try:
            add(date.fromisoformat(match.group(1)))
        except ValueError:
            continue
    for match in _MONTH_DATE_RE.finditer(cleaned):
        add(_parse_one_date(match.group(1)))
    # Whole-string parse as a last resort for a single bare date.
    if not found:
        add(_parse_one_date(cleaned))
    return found


def parse_date_loose(text: str) -> date | None:
    """Parse the first date from common formats / embedded ISO / month names."""
    dates = extract_dates(text)
    return dates[0] if dates else None


def date_in_quote(value: str | date, quote: str) -> bool:
    """True when ``value`` equals any date extracted from ``quote``."""
    target = value if isinstance(value, date) else parse_date_loose(str(value))
    if target is None:
        return False
    return target in extract_dates(quote)


def dates_equal(a: str | date, b: str | date) -> bool:
    if isinstance(b, str) and len(extract_dates(b)) > 1:
        return date_in_quote(a, b)
    if isinstance(a, str) and len(extract_dates(a)) > 1:
        return date_in_quote(b, a)
    da = a if isinstance(a, date) else parse_date_loose(str(a))
    db = b if isinstance(b, date) else parse_date_loose(str(b))
    if da is None or db is None:
        return False
    return da == db


def format_coupon_pct(coupon: Decimal | str | float) -> str:
    """Coupon as ``4.329%`` (strip DB Numeric padding, keep meaningful decimals)."""
    text = f"{Decimal(str(coupon)):.6f}".rstrip("0").rstrip(".")
    return f"{text}%"


def instrument_identified_in_quote(
    *,
    quote: str,
    name: str | None,
    cusip: str | None,
    coupon: Decimal | str | float | None,
    maturity_year: int | None,
) -> bool:
    """CUSIP, full name, or both coupon (e.g. ``4.329%``) and maturity year."""
    q = quote
    q_compact = re.sub(r"\s+", "", q)
    if cusip:
        cusip_compact = re.sub(r"\s+", "", str(cusip))
        if cusip_compact and cusip_compact in q_compact:
            return True
    if name and name in q:
        return True
    if coupon is not None and maturity_year is not None:
        token = format_coupon_pct(coupon)
        year = str(maturity_year)
        if token in q and year in q:
            return True
    return False
