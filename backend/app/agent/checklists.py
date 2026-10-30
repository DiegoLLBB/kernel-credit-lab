"""Required checklist item keys per event kind (CONTEXT.md §9.3).

Every kind also requires ``counter_evidence``. These are the public coverage rules;
replay scoring against ``ReplayExpectation`` is separate and never drives a refusal.
"""

from __future__ import annotations

from typing import Final

# Shared counter-evidence key required on every kind.
COUNTER_EVIDENCE: Final = "counter_evidence"

REQUIRED_ITEMS: Final[dict[str, frozenset[str]]] = {
    "redemption": frozenset(
        {
            "instrument",
            "amount",
            "redemption_date",
            "price_basis",
            "conditions",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "tender_results": frozenset(
        {
            "instrument",
            "amount_accepted",
            "amount_remaining",
            "settlement_date",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "new_issuance": frozenset(
        {
            "instrument",
            "amount",
            "coupon_or_rate",
            "maturity",
            "issue_date",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "new_loan": frozenset(
        {
            "instrument",
            "amount",
            "rate",
            "maturity",
            "borrower",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "loan_repaid": frozenset(
        {
            "instrument",
            "amount",
            "repayment_date",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "maturity": frozenset(
        {
            "instrument",
            "amount",
            "maturity_date",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "periodic_filing": frozenset(
        {
            "scope",
            "period_end",
            "debt_changes",
            "maturity_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "rate_update": frozenset(
        {
            "series",
            "as_of",
            "rate_reset_effect",
            COUNTER_EVIDENCE,
        }
    ),
    "credit_facility": frozenset(
        {
            "facility",
            "size",
            "maturity",
            "drawn_amount",
            COUNTER_EVIDENCE,
        }
    ),
    "equity_issuance": frozenset(
        {
            "security",
            "size",
            "use_of_proceeds",
            COUNTER_EVIDENCE,
        }
    ),
    "other": frozenset({"subject", "effect", COUNTER_EVIDENCE}),
}

EVENT_KINDS: Final[frozenset[str]] = frozenset(REQUIRED_ITEMS)


def required_keys(kind: str) -> frozenset[str]:
    return REQUIRED_ITEMS.get(kind, frozenset({COUNTER_EVIDENCE, "subject"}))


def missing_required(kind: str, item_keys: set[str]) -> list[str]:
    return sorted(required_keys(kind) - item_keys)
