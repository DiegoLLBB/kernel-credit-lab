"""Display formatters and live outcome vocabulary."""

from __future__ import annotations

from app.ingest.live import (
    _normalize_live_outcome,
    form_words,
)
from app.insight.display import format_date_display, format_usd_display, format_usd_wall
from app.insight.generate import _template_impact


def test_format_usd_display_bn_and_m():
    assert format_usd_display(30_421_875_000) == "$30.4bn"
    assert format_usd_display(12_921_875_000) == "$12.9bn"
    assert format_usd_display(925_000_000) == "$925M"


def test_format_usd_wall_millions():
    assert format_usd_wall(11_889_000_000) == "$11,889M"
    assert format_usd_wall(10_639_000_000) == "$10,639M"


def test_format_date_display():
    from datetime import date

    assert format_date_display(date(2026, 11, 2)) == "2 Nov 2026"


def test_form_words():
    assert form_words("424B2") == "prospectus supplement (bond offering)"
    assert form_words("10-Q") == "quarterly report"
    assert form_words("8-K") == "current report"


def test_live_outcome_vocabulary():
    assert _normalize_live_outcome("alerted") == "raised alert"
    assert _normalize_live_outcome("logged") == "reviewed, no alert"
    assert _normalize_live_outcome("needs_input") == "needs an analyst"
    assert _normalize_live_outcome("refused") == "needs an analyst"
    assert _normalize_live_outcome("paused") == "paused"


def test_template_impact_uses_display_strings():
    sentence = _template_impact(
        {
            "issuer": "Amazon",
            "wall_deltas": [
                {
                    "bucket": "FY2029",
                    "before": 12_921_875_000,
                    "after": 30_421_875_000,
                    "before_display": "$12.9bn",
                    "after_display": "$30.4bn",
                    "change_abs": 17_500_000_000,
                }
            ],
        }
    )
    assert sentence == "Amazon: FY2029 rises from $12.9bn to $30.4bn."
