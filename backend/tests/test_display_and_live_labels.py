"""Display formatters and live outcome vocabulary."""

from __future__ import annotations

from app.ingest.live import (
    _normalize_live_outcome,
    form_words,
)
from app.insight.display import (
    format_date_display,
    format_duration_ms,
    format_usd_display,
    format_usd_wall,
    round_sig_half_up,
)
from app.insight.generate import _template_impact


def test_round_sig_half_up():
    assert float(round_sig_half_up(11.25, 3)) == 11.3
    assert float(round_sig_half_up(30.421875, 3)) == 30.4
    assert float(round_sig_half_up(105.7738, 3)) == 106.0
    assert float(round_sig_half_up(7.75, 3)) == 7.75


def test_format_usd_display_bn_and_m():
    assert format_usd_display(30_421_875_000) == "$30.4bn"
    assert format_usd_display(12_921_875_000) == "$12.9bn"
    assert format_usd_display(11_250_000_000) == "$11.3bn"
    assert format_usd_display(925_000_000) == "$925M"


def test_format_usd_wall_millions():
    assert format_usd_wall(11_889_000_000) == "$11,889M"
    assert format_usd_wall(10_639_000_000) == "$10,639M"


def test_format_date_display():
    from datetime import date

    assert format_date_display(date(2026, 11, 2)) == "2 Nov 2026"


def test_format_duration_ms():
    assert format_duration_ms(428_536) == "7 min 9 s"
    assert format_duration_ms(5_000) == "5 s"


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


def test_template_impact_lists_buckets_in_order():
    sentence = _template_impact(
        {
            "issuer_short": "Amazon",
            "issuer_key": "amazon",
            "title": "Amazon closed GBP 4.25bn four-tranche notes offering",
            "wall_deltas": [
                {
                    "bucket": "after FY2030",
                    "before": 105_773_800_000,
                    "after": 109_769_750_000,
                    "before_display": "$106bn",
                    "after_display": "$110bn",
                    "change_abs": 3_995_950_000,
                },
                {
                    "bucket": "FY2029",
                    "before": 11_250_000_000,
                    "after": 12_936_750_000,
                    "before_display": "$11.3bn",
                    "after_display": "$12.9bn",
                    "change_abs": 1_686_750_000,
                },
            ],
            "term_loan_display": None,
        }
    )
    assert sentence is not None
    assert sentence.startswith("Amazon's ")
    assert "debt due in 2029 rises from $11.3bn to $12.9bn" in sentence
    assert "after 2030 rises from $106bn to $110bn" in sentence
    # 2029 before after-2030
    assert sentence.index("2029") < sentence.index("after 2030")
    assert "GBP notes" in sentence


def test_template_impact_term_loan_hook():
    sentence = _template_impact(
        {
            "issuer_short": "Amazon",
            "title": "Amazon 10-Q: $17.5bn Term Loan drawn Sep 2026 due Sep 2029",
            "term_loan_display": "$17.5bn",
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
    assert sentence is not None
    assert "Amazon's debt due in 2029 rises from $12.9bn to $30.4bn" in sentence
    assert "after it drew a $17.5bn term loan" in sentence
