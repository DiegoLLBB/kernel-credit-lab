"""Chart values vs SEC filings / credit metrics (real fixture XBRL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.charts.crosscheck import (
    chart_value_to_usd,
    crosscheck_chart,
    match_metric_family,
    parse_category_period,
)
from app.charts.facts_cache import fixture_companyfacts
from app.db import Base
from app.models import ChartReading, CreditMetric, Filing, Issuer


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def test_parse_category_period_variants() -> None:
    assert parse_category_period("3Q26") == {
        "kind": "quarter",
        "year": 2026,
        "quarter": 3,
        "fiscal_label": "Q3 2026",
        "fp": "Q3",
    }
    assert parse_category_period("Q2 2026")["fiscal_label"] == "Q2 2026"
    assert parse_category_period("FY2025")["fiscal_label"] == "FY2025"
    assert parse_category_period("2025")["kind"] == "fy"
    assert parse_category_period("weird") is None


def test_match_metric_family_by_keywords() -> None:
    debt = match_metric_family("Consolidated Debt", "$B")
    assert debt is not None and debt["id"] == "total_debt"
    sales = match_metric_family("Net Sales", "$B")
    assert sales is not None and sales["id"] == "revenue"
    assert match_metric_family("Maturity Profile", "$B") is None
    assert match_metric_family("Random Bars", "$B") is None


def test_unit_scale_conversion() -> None:
    assert chart_value_to_usd(54.1, "$B") == Decimal("54100000000")
    assert chart_value_to_usd(180.2, "$B") == Decimal("180200000000")
    assert chart_value_to_usd(1.95, "$", per_share=True) == Decimal("1.95")
    assert chart_value_to_usd(54.1, "$") is None  # scale unclear


def _seed_issuer(session: Session, *, key: str, cik: str, name: str) -> tuple[Issuer, Filing]:
    iss = Issuer(key=key, name=name, cik=cik, ticker=key[:4].upper())
    session.add(iss)
    session.flush()
    filing = Filing(
        issuer_id=iss.id,
        form_type="presentation",
        title=f"{name} deck",
        filed_at=date(2026, 10, 1),
        url=f"https://example.test/{key}-deck.pdf",
        content_hash=f"test-hash-{key}",
    )
    session.add(filing)
    session.flush()
    return iss, filing


def test_boeing_consolidated_debt_agrees_with_credit_metric(db_session: Session) -> None:
    """Hand-checked: Boeing FY2025 total debt ≈ $54,098m (LongTermDebt + finance lease)."""
    iss, filing = _seed_issuer(
        db_session, key="boeing", cik="0000012927", name="The Boeing Company"
    )
    # Printed FY2025 total debt 54,098 million USD.
    db_session.add(
        CreditMetric(
            issuer_id=iss.id,
            metric="total_debt",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            value=Decimal("54098000000"),
            unit="USD",
            source_tag="LongTermDebt+FinanceLeaseLiability",
            accession="0000012927-26-000014",
            filed=date(2026, 1, 30),
        )
    )
    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=4,
        title="Consolidated Debt",
        unit="$B",
        categories=["FY2025", "Q3 2026"],
        values=[54.098, 45.6],
        verified=True,
        verification_detail={"status": "verified"},
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()

    facts = fixture_companyfacts(iss.cik)
    assert facts is not None
    result = crosscheck_chart(db_session, chart, issuer=iss, companyfacts=facts)
    assert result.metric_id == "total_debt"
    fy = next(v for v in result.values if v.category == "FY2025")
    assert fy.status == "agree", fy
    assert fy.xbrl_value == pytest.approx(54_098_000_000, rel=0.01)
    assert fy.filing_url and "sec.gov" in fy.filing_url
    print(
        "Boeing Consolidated Debt FY2025:",
        f"chart={fy.chart_value} xbrl={fy.xbrl_value} status={fy.status}",
    )


def test_amazon_net_sales_vs_xbrl_fixture(db_session: Session) -> None:
    """Hand-checked Amazon Q3 2026 quarterly revenue from companyfacts (~$180–201B range).

    Fixture carries multiple durations for the same fp; crosscheck prefers ~90-day
    points. Using the printed chart-style $B quarterly figure.
    """
    iss, filing = _seed_issuer(db_session, key="amazon", cik="0001018724", name="Amazon.com, Inc.")
    facts = fixture_companyfacts(iss.cik)
    assert facts is not None
    # Find a Q3 2026 quarterly (~90d) revenue point for the hand-check.
    tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    pts = facts["facts"]["us-gaap"][tag]["units"]["USD"]
    q3 = [
        p
        for p in pts
        if p.get("fp") == "Q3" and str(p.get("end", "")).startswith("2026-09") and p.get("start")
    ]
    assert q3, "fixture missing Q3 2026 revenue"

    # Prefer short duration
    def dur(p):
        from datetime import date as d

        return (d.fromisoformat(p["end"][:10]) - d.fromisoformat(p["start"][:10])).days

    q3_qtr = sorted(q3, key=lambda p: abs(dur(p) - 90))[0]
    hand = Decimal(str(q3_qtr["val"]))
    assert 60 <= dur(q3_qtr) <= 120, f"expected quarterly duration, got {dur(q3_qtr)}"
    chart_b = float((hand / Decimal("1000000000")).quantize(Decimal("0.1")))

    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=2,
        title="Net Sales",
        unit="$B",
        categories=["Q3 2026"],
        values=[chart_b],  # chart-style one-decimal $B
        verified=True,
        verification_detail={"status": "verified"},
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()

    result = crosscheck_chart(db_session, chart, issuer=iss, companyfacts=facts)
    assert result.metric_id == "revenue"
    row = result.values[0]
    print(
        "Amazon Net Sales Q3 2026:",
        f"chart={row.chart_value} xbrl={row.xbrl_value} status={row.status} "
        f"reason={row.reason} hand={hand} duration_days={dur(q3_qtr)}",
    )
    assert row.status == "agree", row
    assert row.xbrl_value == pytest.approx(float(hand), rel=0.01)
    assert row.category == "Q3 2026"


def test_disagree_is_surfaced(db_session: Session) -> None:
    iss, filing = _seed_issuer(db_session, key="boeing2", cik="0000012927", name="Boeing")
    db_session.add(
        CreditMetric(
            issuer_id=iss.id,
            metric="total_debt",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            value=Decimal("54098000000"),
            unit="USD",
            accession="0000012927-26-000014",
            filed=date(2026, 1, 30),
        )
    )
    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=1,
        title="Consolidated Debt",
        unit="$B",
        categories=["FY2025"],
        values=[40.0],  # deliberately wrong
        verified=True,
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()
    result = crosscheck_chart(db_session, chart, issuer=iss, companyfacts=None)
    assert result.summary_status == "differ"
    assert result.values[0].status == "disagree"
    assert result.values[0].xbrl_value == pytest.approx(54_098_000_000)
    print(
        "Disagree case:",
        result.values[0].chart_value,
        "vs",
        result.values[0].xbrl_value,
    )


def test_segment_chart_not_comparable(db_session: Session) -> None:
    iss, filing = _seed_issuer(db_session, key="amzn-seg", cik="0001018724", name="Amazon")
    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=1,
        title="Net sales by segment",
        unit="$B",
        categories=["AWS", "North America"],
        values=[90.0, 40.0],
        verified=True,
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()
    result = crosscheck_chart(db_session, chart, issuer=iss, companyfacts=None)
    assert result.summary_status == "not_comparable"
    assert result.values[0].reason == "segment"


def test_segment_inferred_when_far_below_consolidated(db_session: Session) -> None:
    """Untitled segment pages (title still 'Net Sales') must not count as filing differs."""
    iss, filing = _seed_issuer(db_session, key="amzn-seg2", cik="0001018724", name="Amazon")
    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=8,
        title="Net Sales",
        unit="$B",
        categories=["Q3 2026"],
        values=[112.9],  # ~half of consolidated ~$202B
        verified=True,
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()
    result = crosscheck_chart(
        db_session,
        chart,
        issuer=iss,
        companyfacts=fixture_companyfacts(iss.cik),
    )
    assert result.summary_status == "not_comparable"
    assert result.values[0].reason == "segment"


def test_period_unclear_not_comparable(db_session: Session) -> None:
    iss, filing = _seed_issuer(db_session, key="amzn2", cik="0001018724", name="Amazon")
    chart = ChartReading(
        issuer_id=iss.id,
        filing_id=filing.id,
        page=1,
        title="Net Sales",
        unit="$B",
        categories=["LTM"],
        values=[180.0],
        verified=True,
        bbox={},
    )
    db_session.add(chart)
    db_session.flush()
    result = crosscheck_chart(
        db_session,
        chart,
        issuer=iss,
        companyfacts=fixture_companyfacts(iss.cik),
    )
    assert result.values[0].status == "not_comparable"
    assert "period unclear" in (result.values[0].reason or "")
