"""Wonder / SAM cost labels must never look like free ($0)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.llm.pricing import (
    COST_UNKNOWN,
    TOTAL_EXCL_UNPRICED,
    aggregate_call_costs,
    cost_label_for_call,
    is_unpriced_model,
)


def test_wonder_and_sam_are_unpriced() -> None:
    assert is_unpriced_model("muse-wonder-1.0")
    assert is_unpriced_model("sam-3.1")
    assert not is_unpriced_model("muse-spark-1.3")


def test_wonder_call_never_shows_zero() -> None:
    assert cost_label_for_call("muse-wonder-1.0", None) == COST_UNKNOWN
    assert cost_label_for_call("muse-wonder-1.0", 0) == COST_UNKNOWN
    assert cost_label_for_call("sam-3.1", Decimal("0")) == COST_UNKNOWN


def test_aggregate_excludes_unpriced() -> None:
    calls = [
        SimpleNamespace(model="muse-wonder-1.0", cost_usd=None),
        SimpleNamespace(model="muse-spark-1.3", cost_usd=Decimal("0.12")),
    ]
    total, unpriced, label = aggregate_call_costs(calls)
    assert total == Decimal("0.12")
    assert unpriced is True
    assert TOTAL_EXCL_UNPRICED in label


def test_aggregate_wonder_only_is_unknown() -> None:
    calls = [SimpleNamespace(model="muse-wonder-1.0", cost_usd=None)]
    total, unpriced, label = aggregate_call_costs(calls)
    assert total is None
    assert unpriced is True
    assert label == COST_UNKNOWN
