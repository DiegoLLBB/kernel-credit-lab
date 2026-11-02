"""Pick us-gaap tags for an onboarded issuer from companyfacts (no guessing values).

For each metric field, candidates are tried in order; the first tag with a recent
full-year USD datapoint is kept. Missing fields get null + an explicit reason.
Debt is required: refuse when neither debt_total nor (debt_current + debt_noncurrent)
has a usable tag.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.metrics.select import select_full_year, select_instant

# Ordered candidates drawn from tags already verified for seed issuers plus
# common us-gaap alternatives. Presence is checked against companyfacts; we do
# not invent amounts.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "debt_total": (
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "DebtInstrumentCarryingAmount",
    ),
    "debt_current": (
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    ),
    "debt_noncurrent": (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermNotesPayable",
        "LongTermDebt",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "Cash"),
    "st_investments": (
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "MarketableSecuritiesCurrent",
    ),
    "opinc": ("OperatingIncomeLoss",),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "interest": ("InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireOtherProductiveAssets",
    ),
    "mat_next12": ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",),
    "mat_year2": ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",),
}

MATURITY_WALL_TAGS: tuple[tuple[str, str], ...] = (
    ("next12", "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"),
    ("year2", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo"),
    ("year3", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree"),
    ("year4", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour"),
    ("year5", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive"),
    (
        "thereafter",
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInRestatementPeriod1",
    ),
)

THEREAFTER_ALTS = (
    "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInRestatementPeriod1",
)

# Flow tags need a FY duration fact; balance-sheet / maturity tags are instants.
_FLOW_FIELDS = frozenset({"opinc", "depreciation", "interest", "ocf", "capex"})


def _has_recent(
    doc: dict[str, Any],
    tag: str,
    *,
    as_of: date,
    flow: bool,
    max_age_days: int = 800,
) -> bool:
    if flow:
        point = select_full_year(doc, tag, as_of=as_of)
    else:
        point = select_instant(doc, tag, as_of=as_of, fp="FY")
        if point is None:
            point = select_instant(doc, tag, as_of=as_of)
    if point is None:
        return False
    return (as_of - point.filed) <= timedelta(days=max_age_days)


def _first_present(
    doc: dict[str, Any],
    candidates: tuple[str, ...],
    *,
    as_of: date,
    flow: bool,
) -> str | None:
    for tag in candidates:
        if _has_recent(doc, tag, as_of=as_of, flow=flow):
            return tag
    return None


def build_xbrl_config(
    doc: dict[str, Any],
    *,
    cik: str,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return a yaml-shaped issuer entry with tags / reasons, or raise if no debt."""
    cutoff = as_of or date.today()
    gaap = (doc.get("facts") or {}).get("us-gaap") or {}
    if not gaap:
        raise ValueError("companyfacts has no us-gaap section; cannot load XBRL debt data")

    entry: dict[str, Any] = {"cik": cik}
    missing: list[dict[str, str]] = []

    debt_current = _first_present(doc, CANDIDATES["debt_current"], as_of=cutoff, flow=False)
    debt_noncurrent = _first_present(doc, CANDIDATES["debt_noncurrent"], as_of=cutoff, flow=False)
    debt_total = _first_present(doc, CANDIDATES["debt_total"], as_of=cutoff, flow=False)

    if debt_current and debt_noncurrent:
        entry["debt_current"] = debt_current
        entry["debt_noncurrent"] = debt_noncurrent
        entry["debt_total"] = None
        entry["debt_total_reason"] = (
            "Using debt_current + debt_noncurrent; debt_total left unset to avoid double-count."
        )
    elif debt_total:
        entry["debt_total"] = debt_total
        entry["debt_current"] = None
        entry["debt_current_reason"] = (
            "Included in debt_total tag; current portion not configured separately."
        )
        entry["debt_noncurrent"] = None
        entry["debt_noncurrent_reason"] = (
            "Included in debt_total tag; noncurrent portion not configured separately."
        )
    else:
        raise ValueError(
            "No usable XBRL debt tags "
            "(need debt_total or debt_current+debt_noncurrent with a recent FY datapoint)"
        )

    for field_name, candidates in CANDIDATES.items():
        if field_name.startswith("debt_"):
            continue
        tag = _first_present(
            doc,
            candidates,
            as_of=cutoff,
            flow=field_name in _FLOW_FIELDS,
        )
        if tag:
            entry[field_name] = tag
        else:
            entry[field_name] = None
            reason = (
                f"No recent FY datapoint among candidates "
                f"({', '.join(candidates)}) in companyfacts."
            )
            entry[f"{field_name}_reason"] = reason
            missing.append({"field": field_name, "reason": reason})

    entry["revolver"] = None
    entry["revolver_reason"] = (
        "Undrawn revolver capacity is not tagged in XBRL; omitted, not estimated."
    )
    entry["_missing"] = missing
    return entry


def pick_thereafter_tag(doc: dict[str, Any], *, as_of: date) -> str | None:
    for tag in THEREAFTER_ALTS:
        if _has_recent(doc, tag, as_of=as_of, flow=False):
            return tag
    return None
