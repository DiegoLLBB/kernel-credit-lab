"""Pick us-gaap tags for an onboarded issuer from companyfacts (no guessing values).

For each metric field, candidates are tried in order; the first tag with a recent
full-year USD datapoint is kept. Missing fields get null + an explicit reason.
Debt is required: refuse when neither debt_total nor (debt_current + debt_noncurrent)
has a usable tag.

Documented flow fallbacks (deterministic):
  opinc — OperatingIncomeLoss; else pretax income + interest expense
          (IncomeLossFromContinuingOperationsBeforeIncomeTaxes* + interest tag)
  D&A  — DepreciationDepletionAndAmortization, DepreciationAndAmortization,
          DepreciationAmortizationAndAccretionNet; else Depreciation +
          AmortizationOfIntangibleAssets (sum)
  interest — InterestExpense, InterestExpenseNonoperating, InterestExpenseDebt;
              InterestPaidNet last resort (labelled as cash interest)
  debt due — LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths /
              …YearTwo (and close family aliases)
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
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        # Some filers (e.g. P&G) stopped tagging the carrying-value concept and
        # only report the statement-of-cash-flows total that includes restricted cash.
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ),
    "st_investments": (
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "MarketableSecuritiesCurrent",
    ),
    "opinc": ("OperatingIncomeLoss",),
    "pretax": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
        "IncomeLossBeforeIncomeTaxes",
    ),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    # Pair summed when no single D&A tag is present.
    "depreciation_pair": ("Depreciation", "AmortizationOfIntangibleAssets"),
    "interest": (
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    ),
    "interest_cash": ("InterestPaidNet", "InterestPaid"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireOtherProductiveAssets",
    ),
    "mat_next12": (
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextRollingTwelveMonths",
        "LongTermDebtCurrent",
    ),
    "mat_year2": (
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInSecondYear",
    ),
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
_FLOW_FIELDS = frozenset({"opinc", "pretax", "depreciation", "interest", "ocf", "capex"})


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


def _pair_present(
    doc: dict[str, Any],
    pair: tuple[str, str],
    *,
    as_of: date,
) -> list[str] | None:
    a, b = pair
    if _has_recent(doc, a, as_of=as_of, flow=True) and _has_recent(doc, b, as_of=as_of, flow=True):
        return [a, b]
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

    simple_fields = (
        "cash",
        "st_investments",
        "ocf",
        "capex",
        "mat_next12",
        "mat_year2",
    )
    for field_name in simple_fields:
        candidates = CANDIDATES[field_name]
        tag = _first_present(
            doc,
            candidates,
            as_of=cutoff,
            flow=field_name in _FLOW_FIELDS,
        )
        if tag:
            entry[field_name] = tag
            if field_name == "mat_next12" and tag != candidates[0]:
                entry[f"{field_name}_reason"] = (
                    f"Fallback tag {tag} (primary {candidates[0]} unavailable)."
                )
            if field_name == "mat_year2" and tag != candidates[0]:
                entry[f"{field_name}_reason"] = (
                    f"Fallback tag {tag} (primary {candidates[0]} unavailable)."
                )
        else:
            entry[field_name] = None
            reason = (
                f"No recent FY datapoint among candidates "
                f"({', '.join(candidates)}) in companyfacts."
            )
            entry[f"{field_name}_reason"] = reason
            missing.append({"field": field_name, "reason": reason})

    # Interest: accrual family first; InterestPaidNet last resort (labelled).
    interest = _first_present(doc, CANDIDATES["interest"], as_of=cutoff, flow=True)
    if interest:
        entry["interest"] = interest
    else:
        cash_int = _first_present(doc, CANDIDATES["interest_cash"], as_of=cutoff, flow=True)
        if cash_int:
            entry["interest"] = cash_int
            entry["interest_reason"] = (
                f"{cash_int} (cash interest paid; accrual InterestExpense* tags unavailable)"
            )
        else:
            entry["interest"] = None
            reason = "No recent FY datapoint among InterestExpense* / InterestPaid* candidates."
            entry["interest_reason"] = reason
            missing.append({"field": "interest", "reason": reason})

    # D&A: single-tag family, else Depreciation + AmortizationOfIntangibleAssets.
    dep = _first_present(doc, CANDIDATES["depreciation"], as_of=cutoff, flow=True)
    if dep:
        entry["depreciation"] = dep
    else:
        pair = _pair_present(
            doc,
            CANDIDATES["depreciation_pair"],  # type: ignore[arg-type]
            as_of=cutoff,
        )
        if pair:
            entry["depreciation"] = pair
            entry["depreciation_reason"] = (
                "Sum of Depreciation + AmortizationOfIntangibleAssets (no combined D&A tag)."
            )
        else:
            entry["depreciation"] = None
            reason = (
                "No recent FY datapoint among D&A candidates "
                f"({', '.join(CANDIDATES['depreciation'])}) or "
                f"{' + '.join(CANDIDATES['depreciation_pair'])}."
            )
            entry["depreciation_reason"] = reason
            missing.append({"field": "depreciation", "reason": reason})

    # Operating income, else pretax + interest for EBITDA rebuild.
    opinc = _first_present(doc, CANDIDATES["opinc"], as_of=cutoff, flow=True)
    if opinc:
        entry["opinc"] = opinc
        entry["pretax"] = None
        entry["ebitda_compose"] = "opinc_plus_da"
    else:
        entry["opinc"] = None
        pretax = _first_present(doc, CANDIDATES["pretax"], as_of=cutoff, flow=True)
        if pretax and entry.get("interest"):
            entry["pretax"] = pretax
            entry["ebitda_compose"] = "pretax_plus_interest_plus_da"
            entry["opinc_reason"] = (
                f"No OperatingIncomeLoss; EBITDA from pre-tax income ({pretax}) "
                f"+ interest ({entry['interest']}) + D&A."
            )
        else:
            entry["pretax"] = None
            entry["ebitda_compose"] = None
            reason = "No OperatingIncomeLoss; pretax+interest fallback also unavailable."
            entry["opinc_reason"] = reason
            missing.append({"field": "opinc", "reason": reason})

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
