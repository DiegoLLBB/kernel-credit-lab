"""Deterministic number matcher: every figure must come from the facts JSON.

A numeric token in model prose matches when it equals a number in the facts
registry within rounding tolerance: half of the token's last decimal place,
scaled (so "$87.2 billion" allows ±$50M, "5.34x" allows ±0.005). Percentages
also match their ratio form ("16.8%" matches 0.1675). Skipped, by design:

- calendar years (bare 1900–2100) and fiscal labels ("Q1", "FY2026", "10-K"),
  which are references, not claims;
- bare integers below 100 with no $, %, x or scale word ("3 sentences",
  "24 months") — the prompt still binds these to the facts, and the Spark
  Open verifier judges meaning, but exactness cannot be checked;
- numbers inside the alert evidence quotes are NOT skipped: quotes are part of
  the facts JSON (their figures are registered), so a misquoted figure fails.

Anything else unmatched is a violation: the sentence is dropped, fail-closed.
"""

from __future__ import annotations

import contextlib
import re
from decimal import Decimal, InvalidOperation

_SCALE_WORDS = {
    "trillion": Decimal(1_000_000_000_000),
    "billion": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "b": Decimal(1_000_000_000),
    "million": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "m": Decimal(1_000_000),
}

_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(?P<sym>\$)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>trillion|billion|million|bn|mm|m(?![a-z])|b(?![a-z])|%)?"
    r"(?P<mult>[x×])?",
    re.I,
)
_FORM_SUFFIX_RE = re.compile(r"-(?=[A-Za-z])")


def _token_value(num: str, unit: str | None) -> tuple[Decimal, Decimal]:
    """(absolute value, tolerance) for one numeric token."""
    digits = num.replace(",", "")
    try:
        base = Decimal(digits)
    except InvalidOperation:
        raise ValueError(f"not a number: {num!r}") from None
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    if unit is None:
        return base, Decimal(1).scaleb(-decimals) / 2 or Decimal("0.5")
    lowered = unit.lower()
    if lowered == "%":
        tol = (Decimal(1).scaleb(-decimals) / 2 or Decimal("0.5")) / 100
        return base / 100, tol
    scale = _SCALE_WORDS[lowered]
    return base * scale, (Decimal(1).scaleb(-decimals) / 2 or Decimal("0.5")) * scale


def find_violations(text: str, registry: list[Decimal]) -> list[str]:
    """Tokens in ``text`` with no match in ``registry`` (empty = all grounded)."""
    violations: list[str] = []
    for match in _TOKEN_RE.finditer(text.replace(" ", " ")):
        token = match.group(0).strip()
        if not token:
            continue
        if _FORM_SUFFIX_RE.match(text[match.end() :]):
            continue  # "10-K", "8-K": filing references, not claims
        num = match.group("num")
        unit = match.group("unit")
        mult = match.group("mult")
        marked = bool(match.group("sym") or unit or mult)
        try:
            value, tol = _token_value(num, unit)
        except ValueError:
            continue
        if value == int(value) and 1900 <= value <= 2100 and not marked:
            continue  # calendar year
        if not marked and value < 100:
            continue  # bare small count; prompt-bound, verifier-judged
        candidates = [(value, tol)]
        if (unit or "").lower() == "%":
            candidates.append((value * 100, tol * 100))  # percent-points form too
        if not any(abs(v - f) <= t for v, t in candidates for f in registry):
            violations.append(token)
    return violations


def collect_numbers(obj: object) -> list[Decimal]:
    """Every numeric leaf of a facts JSON blob (the quotable registry)."""
    found: list[Decimal] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            found.append(Decimal(str(node)))
        elif isinstance(node, Decimal):
            found.append(node)
        elif isinstance(node, str):
            with contextlib.suppress(InvalidOperation):
                found.append(Decimal(node))

    walk(obj)
    return found
