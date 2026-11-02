"""Model price helpers: Wonder and SAM have no published $/token."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# Meta has not published public $/token for these; never treat missing/zero as free.
UNPRICED_MODELS = frozenset(
    {
        "muse-wonder-1.0",
        "sam-3.1",
    }
)

COST_UNKNOWN = "cost unknown (price not published)"
TOTAL_EXCL_UNPRICED = "excl. unpriced calls"


def is_unpriced_model(model: str | None) -> bool:
    if not model:
        return False
    name = str(model).strip().lower()
    if name in UNPRICED_MODELS:
        return True
    return name.startswith("muse-wonder") or name.startswith("sam-3")


def cost_label_for_call(model: str | None, cost_usd: Any) -> str:
    """Human label for one model call."""
    if is_unpriced_model(model):
        return COST_UNKNOWN
    if cost_usd is None:
        return COST_UNKNOWN if is_unpriced_model(model) else "—"
    try:
        v = float(cost_usd)
    except (TypeError, ValueError):
        return "—"
    if v == 0.0 and is_unpriced_model(model):
        return COST_UNKNOWN
    if v < 1:
        return f"${v:.4f}"
    return f"${v:.2f}"


def aggregate_call_costs(
    calls: list[Any],
) -> tuple[Decimal | None, bool, str]:
    """Sum priced calls; flag when Wonder/SAM (or null-priced) calls are present.

    Returns ``(priced_total_or_none, has_unpriced, display_string)``.
    """
    priced = Decimal("0")
    any_priced = False
    has_unpriced = False
    for row in calls:
        model = getattr(row, "model", None)
        if isinstance(row, dict):
            model = row.get("model")
            cost = row.get("cost_usd")
        else:
            cost = getattr(row, "cost_usd", None)
        if is_unpriced_model(str(model) if model else None):
            has_unpriced = True
            continue
        if cost is None:
            has_unpriced = True
            continue
        try:
            priced += Decimal(str(cost))
            any_priced = True
        except Exception:
            has_unpriced = True
    if not any_priced and has_unpriced:
        return None, True, COST_UNKNOWN
    if not any_priced:
        return None, has_unpriced, "—"
    total = priced
    if has_unpriced:
        label = (
            f"${float(total):.4f} ({TOTAL_EXCL_UNPRICED})"
            if total < 1
            else (f"${float(total):.2f} ({TOTAL_EXCL_UNPRICED})")
        )
        return total, True, label
    label = f"${float(total):.4f}" if total < 1 else f"${float(total):.2f}"
    return total, False, label
