"""Unit tests for fiscal-year maturity bucket assignment."""

from datetime import date

from app.tools.maturity import bucket_for

# Oracle FY ends 31 May.
ORACLE_BUCKETS = [
    {"label": "FY2026", "period_end": "2026-05-31", "amount": "7309000000"},
    {"label": "FY2027", "period_end": "2027-05-31", "amount": "5743000000"},
    {"label": "FY2028", "period_end": "2028-05-31", "amount": "10145000000"},
    {"label": "FY2029", "period_end": "2029-05-31", "amount": "2000000000"},
    {"label": "FY2030", "period_end": "2030-05-31", "amount": "7250000000"},
    {"label": "after FY2030", "period_end": None, "amount": "60500000000"},
]

# Intel FY ends late December (27 Dec).
INTEL_BUCKETS = [
    {"label": "FY2026", "period_end": "2026-12-27", "amount": "2500000000"},
    {"label": "FY2027", "period_end": "2027-12-27", "amount": "3826000000"},
    {"label": "FY2028", "period_end": "2028-12-27", "amount": "3173000000"},
    {"label": "FY2029", "period_end": "2029-12-27", "amount": "3288000000"},
    {"label": "FY2030", "period_end": "2030-12-27", "amount": "2750000000"},
    {"label": "after FY2030", "period_end": None, "amount": "31698000000"},
]


def test_oracle_may_fiscal_year_boundaries() -> None:
    # Still in FY2028 (ends 31 May 2028).
    assert bucket_for(date(2028, 3, 25), ORACLE_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 5, 31), ORACLE_BUCKETS) == "FY2028"
    # Day after FY2028 end → FY2029.
    assert bucket_for(date(2028, 6, 1), ORACLE_BUCKETS) == "FY2029"
    assert bucket_for(date(2028, 8, 15), ORACLE_BUCKETS) == "FY2029"
    assert bucket_for(date(2029, 5, 31), ORACLE_BUCKETS) == "FY2029"


def test_intel_late_december_year_end() -> None:
    assert bucket_for(date(2028, 12, 15), INTEL_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 12, 27), INTEL_BUCKETS) == "FY2028"
    assert bucket_for(date(2028, 12, 28), INTEL_BUCKETS) == "FY2029"
    assert bucket_for(date(2029, 1, 1), INTEL_BUCKETS) == "FY2029"
    assert bucket_for(date(2030, 12, 27), INTEL_BUCKETS) == "FY2030"
    assert bucket_for(date(2030, 12, 28), INTEL_BUCKETS) == "after FY2030"
    assert bucket_for(date(2035, 6, 1), INTEL_BUCKETS) == "after FY2030"


def test_bucket_for_empty_and_before_first() -> None:
    assert bucket_for(date(2028, 1, 1), []) is None
    # Open on the left: anything on or before the first period_end is FY2026.
    assert bucket_for(date(2020, 1, 1), ORACLE_BUCKETS) == "FY2026"


def test_after_bucket_absent_returns_none() -> None:
    dated_only = [b for b in INTEL_BUCKETS if b["period_end"] is not None]
    assert bucket_for(date(2031, 1, 1), dated_only) is None
