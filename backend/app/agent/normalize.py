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
# Numeric calendar dates (US MM/DD/YYYY preferred; DD/MM/YYYY when month would be invalid).
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
# Month + year with no day (e.g. "March 2027"); not a substring of "March 15, 2027".
_MONTH_YEAR_ONLY_RE = re.compile(
    r"\b(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(?P<year>\d{4})\b",
    re.I,
)
# YYYY-MM not followed by -DD.
_YYYY_MM_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})(?!-\d)")
_MONTH_NAME_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

MIN_QUOTE_WORDS = 8
MIN_QUOTE_CHARS = 40
SCALE_MILLIONS = Decimal("1000000")
SCALE_BILLIONS = Decimal("1000000000")


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


def detect_amount_scale(quote: str) -> Decimal | None:
    """Return 1e6 / 1e9 when ``quote`` states amounts are in millions / billions."""
    lowered = normalize_for_quote_match(quote).lower()
    if re.search(r"\bin\s+billions?\b", lowered):
        return SCALE_BILLIONS
    if re.search(r"\bin\s+millions?\b", lowered):
        return SCALE_MILLIONS
    return None


def amount_in_quote(
    value: str | Decimal | int | float,
    quote: str,
    *,
    scale: Decimal | None = None,
) -> bool:
    """True when ``value`` equals any amount in ``quote``.

    With ``scale`` (e.g. 1_000_000 from an "(in millions)" heading fact), also
    accept a number in ``quote`` equal to ``value / scale`` (table units).
    """
    target = coerce_amount(value)
    if target is None:
        return False
    numbers = extract_numbers(quote)
    if any(n == target for n in numbers):
        return True
    if scale is not None and scale != 0:
        try:
            scaled = (target / scale).normalize()
        except InvalidOperation:
            return False
        # Compare without trailing zeros (1000 vs 1000.0).
        return any(n == scaled or n == target / scale for n in numbers)
    return False


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


def _parse_numeric_slash_date(chunk: str) -> date | None:
    """Parse ``MM/DD/YYYY`` or ``DD/MM/YYYY`` (prefer US when both are valid)."""
    cleaned = normalize_for_quote_match(chunk)
    # Prefer US order first (issuer docs / press releases).
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def extract_month_years(text: str) -> list[tuple[int, int]]:
    """Month-year mentions without a day (e.g. ``March 2027``, ``2027-03``)."""
    cleaned = normalize_for_quote_match(text)
    found: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(year: int, month: int) -> None:
        if 1 <= month <= 12 and (year, month) not in seen:
            seen.add((year, month))
            found.append((year, month))

    for match in _MONTH_YEAR_ONLY_RE.finditer(cleaned):
        month = _MONTH_NAME_TO_NUM.get(match.group("month").lower())
        if month is None:
            continue
        add(int(match.group("year")), month)
    for match in _YYYY_MM_RE.finditer(cleaned):
        add(int(match.group("year")), int(match.group("month")))
    return found


def extract_dates(text: str) -> list[date]:
    """All recognisable day-precision dates in ``text`` (ISO and month-name forms)."""
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
    for match in _NUMERIC_DATE_RE.finditer(cleaned):
        add(_parse_numeric_slash_date(match.group(1)))
    # Whole-string parse as a last resort for a single bare date.
    if not found:
        add(_parse_one_date(cleaned))
    return found


def parse_date_with_precision(text: str) -> tuple[date | None, str | None]:
    """Parse a date; precision is ``day``, ``month``, or None if unparseable.

    Month-only inputs (``March 2027``, ``2027-03``) become the 1st of that month.
    """
    raw = (text or "").strip()
    if not raw:
        return None, None
    # Prefer day-precision when present.
    day_dates = extract_dates(raw)
    if day_dates:
        return day_dates[0], "day"
    months = extract_month_years(raw)
    if months:
        year, month = months[0]
        return date(year, month, 1), "month"
    # Bare YYYY-MM as the entire string (already covered by extract_month_years).
    return None, None


def parse_date_loose(text: str) -> date | None:
    """Parse the first date from common formats / ISO / month names / YYYY-MM."""
    d, _prec = parse_date_with_precision(text)
    return d


def date_in_quote(value: str | date, quote: str) -> bool:
    """True when ``value`` matches a day date in ``quote``, or falls in a named month."""
    if isinstance(value, date):
        target = value
    else:
        target, _ = parse_date_with_precision(str(value))
    if target is None:
        return False
    if target in extract_dates(quote):
        return True
    return any(
        year == target.year and month == target.month for year, month in extract_month_years(quote)
    )


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
