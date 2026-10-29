"""Materiality rules (CONTEXT.md §8.3), applied per 2026–2029 wall bucket (PROGRESS decision 8)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

MATERIAL_BUCKETS = ("FY2026", "FY2027", "FY2028", "FY2029")
WALL_HORIZON_END_YEAR = 2029
ABS_THRESHOLD = Decimal("500000000")  # $500M
PCT_THRESHOLD = Decimal("0.10")  # 10%
# Debt "missing from the bond list": issued more than this many days before discovery.
MISSING_DEBT_LOOKBACK_DAYS = 7
LONG_DATED_JUDGEMENT_REFUSAL = (
    "long-dated issuance outside the 2026–2029 wall is not material by judgement; log it"
)


def _bucket_total(wall: dict[str, Any], label: str) -> Decimal:
    buckets = wall.get("buckets") or {}
    bucket = buckets.get(label) or {}
    raw = bucket.get("total_outstanding", 0)
    return Decimal(str(raw or 0))


def is_missing_from_bond_list(*, issue_date: date, available_from: date) -> bool:
    """True when the instrument existed well before Kernel could see the event.

    New issuance (issue_date within 7 days of ``available_from``) is judged only by
    the per-bucket wall rules, not this flag.
    """
    return issue_date < available_from - timedelta(days=MISSING_DEBT_LOOKBACK_DAYS)


def _maturity_year(diff: dict[str, Any]) -> int | None:
    raw = diff.get("maturity")
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    try:
        return date.fromisoformat(text[:10]).year
    except ValueError:
        pass
    # Year-only / YYYY-MM from propose_change.
    try:
        return int(text[:4])
    except ValueError:
        return None


def only_long_dated_add_instruments(proposed: list[Any]) -> bool:
    """True when every proposed change is ``add_instrument`` maturing after FY2029.

    Empty proposed lists return False so judgement upgrades remain available for
    non-debt events (e.g. equity raises that change liquidity with no StateChange).
    """
    if not proposed:
        return False
    for change in proposed:
        diff = getattr(change, "proposed_diff", None) or {}
        if not isinstance(diff, dict):
            return False
        if diff.get("action") != "add_instrument":
            return False
        year = _maturity_year(diff)
        if year is None or year <= WALL_HORIZON_END_YEAR:
            return False
    return True


def assess_materiality(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    new_debt_not_in_list: bool = False,
    contradiction: bool = False,
) -> dict[str, Any]:
    """Return ``{material: bool, reasons: list[str]}`` from before/after metric walls.

    New issuance that merely adds a named instrument is judged by the 2026–2029
    bucket thresholds alone. ``new_debt_not_in_list`` is reserved for debt that
    already existed (issue_date more than 7 days before discovery).
    """
    reasons: list[str] = []

    if new_debt_not_in_list:
        reasons.append("debt appears that was not in the bond list")
    if contradiction:
        reasons.append("verifier or critic found a contradiction with Kernel's own data")

    for label in MATERIAL_BUCKETS:
        before_amt = _bucket_total(before, label)
        after_amt = _bucket_total(after, label)
        delta = abs(after_amt - before_amt)
        if delta >= ABS_THRESHOLD:
            reasons.append(f"{label} wall changed by ${delta:,.0f} (≥ $500M)")
            continue
        if before_amt > 0:
            pct = delta / before_amt
            if pct >= PCT_THRESHOLD:
                reasons.append(f"{label} wall changed by {pct * 100:.1f}% (≥ 10%)")

    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    return {"material": bool(uniq), "reasons": uniq}
