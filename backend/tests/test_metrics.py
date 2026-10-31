"""Engine tests: selection, TTM, point-in-time, maths, missing, loader.

Selection/TTM use small synthetic companyfacts documents (exact arithmetic);
per-company truth comes from tests/test_metrics_printed.py against the recorded
fixtures. Loader tests use SQLite in-memory + fixture-backed fetch.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.metrics.compute import METRIC_IDS, MetricRow, _coverage_row, _leverage_row, compute_issuer
from app.metrics.config import load_metric_config
from app.metrics.loader import load_metrics, upsert_rows
from app.metrics.refresh import refresh_issuer_metrics
from app.metrics.select import (
    iter_datapoints,
    select_full_year,
    select_instant,
    trailing_twelve_months,
)
from app.models import CreditMetric, Issuer

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
CUTOFF = date(2026, 10, 31)


def load_fixture(cik: str) -> dict:
    return json.loads((FIXTURES / f"CIK{cik}.json").read_bytes())


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def metric_config():
    return load_metric_config()


def fetch_from_fixtures(cik: str) -> dict:
    return load_fixture(f"{int(cik):010d}")


def seed_issuers(session: Session) -> None:
    for key, cik in [
        ("duke_corp", "1326160"),
        ("duke_progress", "17797"),
        ("oracle", "1341439"),
        ("verizon", "732712"),
        ("boeing", "12927"),
        ("intel", "50863"),
        ("amazon", "1018724"),
    ]:
        session.add(Issuer(key=key, name=key, cik=cik))
    session.commit()


# --- config ---


def test_config_covers_all_seed_issuers(metric_config):
    assert set(metric_config.issuers) == {
        "duke_corp",
        "duke_carolinas",
        "duke_progress",
        "duke_florida",
        "oracle",
        "verizon",
        "boeing",
        "intel",
        "amazon",
    }


def test_config_tags_exist_in_fixtures(metric_config):
    """Every configured tag must exist in that issuer's recorded facts."""
    for key, cfg in metric_config.issuers.items():
        if not cfg.usable:
            assert cfg.unusable_reason, key
            continue
        # One complete debt path: either a total tag or the current+noncurrent pair.
        assert cfg.tag("debt_total") or (cfg.tag("debt_current") and cfg.tag("debt_noncurrent")), (
            f"{key} has no debt path"
        )
        doc = load_fixture(f"{int(cfg.cik):010d}")
        facts = doc["facts"]["us-gaap"]
        for field_name, tag in cfg.tags.items():
            if tag is None:
                if field_name in ("debt_current", "debt_noncurrent", "debt_total"):
                    continue  # alternative debt representation, not a gap
                assert cfg.reason(field_name), f"{key}.{field_name} needs a reason"
            else:
                assert tag in facts, f"{key}.{field_name}={tag} not in fixture"


def test_subsidiaries_unusable_with_reasons(metric_config):
    for key in ("duke_progress", "duke_carolinas", "duke_florida"):
        cfg = metric_config.for_issuer(key)
        assert not cfg.usable
        assert cfg.unusable_reason
        assert compute_issuer({}, cfg, as_of=CUTOFF) == []


# --- synthetic selection tests ---


def _doc(*entries: dict) -> dict:
    units: dict[str, list] = {}
    for entry in entries:
        units.setdefault(entry.pop("_unit"), []).append(entry)
    return {"facts": {"us-gaap": {"T": {"units": units}}}}


def _dp(val, end, filed, start=None, fy=None, fp="", form="10-K", unit="USD"):
    return {
        "val": val,
        "end": end,
        "filed": filed,
        "start": start,
        "fy": fy,
        "fp": fp,
        "form": form,
        "accn": "0000000000-00-000000",
        "_unit": unit,
    }


def test_point_in_time_hides_future_filings():
    doc = _doc(
        _dp(100, "2025-12-31", "2026-02-26", fy=2025, fp="FY", form="10-K"),
        _dp(200, "2025-12-31", "2026-05-05", fy=2025, fp="Q1", form="10-Q"),
    )
    early = select_instant(doc, "T", as_of=date(2026, 3, 1), period_end=date(2025, 12, 31))
    assert early is not None and early.value == 100
    late = select_instant(doc, "T", as_of=date(2026, 6, 1), period_end=date(2025, 12, 31))
    assert late is not None and late.value == 200  # amendment/restatement wins


def test_ttm_exact_arithmetic():
    doc = _doc(
        _dp(1000, "2024-12-31", "2025-02-20", start="2024-01-01", fy=2024, fp="FY"),
        _dp(300, "2025-03-31", "2025-05-01", start="2025-01-01", fy=2025, fp="Q1"),
        _dp(250, "2024-03-31", "2024-05-01", start="2024-01-01", fy=2024, fp="Q1"),
    )
    ttm = trailing_twelve_months(doc, "T", as_of=date(2025, 6, 1))
    assert ttm is not None and not ttm.is_full_year_only
    assert ttm.value == 1050  # 1000 + 300 - 250


def test_ttm_uses_ytd_duration_not_quarter():
    """A Q2 filing carries 3-month (quarter) and 6-month (YTD) facts; use YTD."""
    doc = _doc(
        _dp(1000, "2024-12-31", "2025-02-20", start="2024-01-01", fy=2024, fp="FY"),
        _dp(150, "2025-06-30", "2025-08-01", start="2025-04-01", fy=2025, fp="Q2"),
        _dp(400, "2025-06-30", "2025-08-01", start="2025-01-01", fy=2025, fp="Q2"),
        _dp(350, "2024-06-30", "2024-08-01", start="2024-01-01", fy=2024, fp="Q2"),
    )
    ttm = trailing_twelve_months(doc, "T", as_of=date(2025, 9, 1))
    assert ttm is not None
    assert ttm.value == 1050  # 1000 + 400 - 350, not 1000 + 150 - 350


def test_ttm_missing_prior_year_returns_none():
    doc = _doc(
        _dp(1000, "2024-12-31", "2025-02-20", start="2024-01-01", fy=2024, fp="FY"),
        _dp(300, "2025-03-31", "2025-05-01", start="2025-01-01", fy=2025, fp="Q1"),
    )
    assert trailing_twelve_months(doc, "T", as_of=date(2025, 6, 1)) is None


def test_ttm_full_year_fallback_before_any_quarter():
    doc = _doc(
        _dp(1000, "2024-12-31", "2025-02-20", start="2024-01-01", fy=2024, fp="FY"),
    )
    ttm = trailing_twelve_months(doc, "T", as_of=date(2025, 3, 1))
    assert ttm is not None and ttm.is_full_year_only and ttm.value == 1000


def test_full_year_select_ignores_quarter_durations():
    doc = _doc(
        _dp(1000, "2024-12-31", "2025-02-20", start="2024-01-01", fy=2024, fp="FY"),
        _dp(300, "2025-03-31", "2025-05-01", start="2025-01-01", fy=2025, fp="Q1"),
    )
    dp = select_full_year(doc, "T", as_of=date(2025, 6, 1), period_end=date(2024, 12, 31))
    assert dp is not None and dp.value == 1000


def test_non_usd_units_ignored():
    doc = _doc(
        _dp(100, "2025-12-31", "2026-02-26", fy=2025, fp="FY", unit="shares"),
    )
    assert iter_datapoints(doc, "T", as_of=date(2026, 3, 1)) == []


# --- compute against fixtures ---


def _fy_rows(metric_config, key, cik, as_of=CUTOFF):
    doc = load_fixture(cik)
    rows = compute_issuer(doc, metric_config.for_issuer(key), as_of=as_of)
    out = {}
    for r in rows:
        out.setdefault((r.metric, r.period_end), r)
    return out


def test_all_twelve_metrics_present_per_period(metric_config):
    rows = _fy_rows(metric_config, "intel", "0000050863")
    ends = sorted({pe for (_, pe) in rows})
    assert len(ends) >= 3  # history + latest quarter
    for end in ends:
        assert {m for (m, pe) in rows if pe == end} == set(METRIC_IDS)


def test_cutoff_before_10k_hides_the_year(metric_config):
    """Duke's FY2025 10-K filed 2026-02-26 is invisible on Feb 1 (Q3'25 still is)."""
    rows = _fy_rows(metric_config, "duke_corp", "0001326160", as_of=date(2026, 2, 1))
    assert "FY2025" not in {r.fiscal_label for r in rows.values()}
    fy_ends = sorted({pe for (m, pe), r in rows.items() if r.fiscal_label.startswith("FY")})
    assert max(fy_ends) == date(2024, 12, 31)
    for r in rows.values():
        assert r.filed is None or r.filed <= date(2026, 2, 1)


def test_every_row_filed_within_cutoff(metric_config):
    rows = _fy_rows(metric_config, "amazon", "0001018724")
    for r in rows.values():
        assert r.filed is None or r.filed <= CUTOFF


def test_duke_fy2025_core_maths(metric_config):
    rows = _fy_rows(metric_config, "duke_corp", "0001326160")
    end = date(2025, 12, 31)

    def get(metric):
        return rows[(metric, end)]

    assert get("total_debt").value == Decimal(87212000000)  # 7,104 + 80,108
    assert get("due_24m").value == get("due_12m").value + Decimal(3639000000)
    assert get("ebitda").value == Decimal(8626000000) + Decimal(7704000000)
    assert get("fcf").value == Decimal(12330000000) - Decimal(14024000000)
    assert get("leverage").value == (Decimal(87212000000) / Decimal(16330000000)).quantize(
        Decimal("0.0001")
    )
    assert get("st_investments").value is None
    assert "cash-only" in (get("st_investments").missing_reason() or "")
    assert get("liquidity_coverage").detail["cash_only"] is True


def test_intel_liquidity_not_cash_only(metric_config):
    rows = _fy_rows(metric_config, "intel", "0000050863")
    row = rows[("liquidity_coverage", date(2025, 12, 27))]
    assert row.detail["cash_only"] is False
    assert row.value == (
        (Decimal(14265000000) + Decimal(23151000000)) / Decimal(6326000000)
    ).quantize(Decimal("0.0001"))


def test_boeing_debt_adds_finance_leases(metric_config):
    rows = _fy_rows(metric_config, "boeing", "0000012927")
    assert rows[("total_debt", date(2025, 12, 31))].value == Decimal(54098000000)


def test_missing_anchor_tag_returns_no_rows():
    doc = _doc(
        _dp(5000, "2025-12-31", "2026-02-26", fy=2025, fp="FY"),
    )
    cfg = load_metric_config().for_issuer("duke_corp")
    rows = compute_issuer(doc, cfg, as_of=date(2026, 10, 31))
    assert rows == []  # tag "T" is not the debt anchor → no periods


def test_non_positive_ebitda_voids_leverage_and_coverage():
    no_debt: list = []
    lev = _leverage_row(
        "leverage",
        date(2025, 12, 31),
        "FY2025",
        Decimal(-5),
        Decimal(100),
        Decimal(10),
        None,
        [],
        no_debt,
    )
    assert lev.value is None and "non-positive" in (lev.missing_reason() or "")
    net = _leverage_row(
        "net_leverage",
        date(2025, 12, 31),
        "FY2025",
        Decimal(0),
        Decimal(100),
        Decimal(10),
        None,
        [],
        no_debt,
    )
    assert net.value is None
    cov = _coverage_row(
        date(2025, 12, 31),
        "FY2025",
        Decimal(-5),
        (Decimal(3), None),
        [],
        load_metric_config().for_issuer("intel"),
        is_quarter=False,
    )
    assert cov.value is None and "non-positive" in (cov.missing_reason() or "")


def test_wall_cross_check_detail(metric_config):
    from decimal import Decimal as D

    doc = load_fixture("0001326160")
    cfg = metric_config.for_issuer("duke_corp")
    wall = {date(2025, 12, 31): (D(7104000000), D(10743000000))}
    rows = {(r.metric, r.period_end): r for r in compute_issuer(doc, cfg, as_of=CUTOFF, wall=wall)}
    due12 = rows[("due_12m", date(2025, 12, 31))]
    assert due12.detail["wall_due"] == "7104000000"
    assert due12.detail["wall_agrees"] is True  # 7,123 vs 7,104 within 2%
    wall_far = {date(2025, 12, 31): (D(1000000000), D(1000000000))}
    rows2 = {
        (r.metric, r.period_end): r for r in compute_issuer(doc, cfg, as_of=CUTOFF, wall=wall_far)
    }
    assert rows2[("due_12m", date(2025, 12, 31))].detail["wall_agrees"] is False


# --- loader ---


def test_loader_upserts_and_is_idempotent(session, metric_config):
    seed_issuers(session)
    stats = load_metrics(
        session, fetch_from_fixtures, config=metric_config, as_of=CUTOFF, with_wall=False
    )
    assert stats["issuers"] == 6  # 6 usable (progress/carolinas/florida skipped)
    assert stats["skipped"] == 3
    assert stats["rows_created"] > 100
    session.commit()
    again = load_metrics(
        session, fetch_from_fixtures, config=metric_config, as_of=CUTOFF, with_wall=False
    )
    assert (again["rows_created"], again["rows_updated"]) == (0, 0)


def test_loader_restricted_to_issuer_keys(session, metric_config):
    seed_issuers(session)
    stats = load_metrics(
        session,
        fetch_from_fixtures,
        config=metric_config,
        as_of=CUTOFF,
        issuer_keys=["intel"],
        with_wall=False,
    )
    assert stats["issuers"] == 1
    (issuer,) = session.scalars(select(Issuer).where(Issuer.key == "intel")).all()
    assert (
        session.scalar(select(CreditMetric).where(CreditMetric.issuer_id == issuer.id)) is not None
    )


def test_loader_skips_unknown_issuer(session, metric_config):
    stats = load_metrics(
        session,
        fetch_from_fixtures,
        config=metric_config,
        as_of=CUTOFF,
        issuer_keys=["nope"],
        with_wall=False,
    )
    assert stats == {"issuers": 0, "rows_created": 0, "rows_updated": 0, "skipped": 1}


def test_upsert_updates_changed_values(session):
    issuer = Issuer(key="intel", name="Intel", cik="50863")
    session.add(issuer)
    session.flush()

    row = MetricRow(
        "total_debt",
        date(2025, 12, 27),
        "FY2025",
        Decimal(1),
        "USD",
        "T",
        "accn",
        date(2026, 1, 23),
        {},
    )
    assert upsert_rows(session, issuer.id, [row]) == (1, 0)
    assert upsert_rows(session, issuer.id, [row]) == (0, 0)
    changed = MetricRow(
        "total_debt",
        date(2025, 12, 27),
        "FY2025",
        Decimal(2),
        "USD",
        "T",
        "accn",
        date(2026, 1, 23),
        {},
    )
    assert upsert_rows(session, issuer.id, [changed]) == (0, 1)


def test_refresh_uses_filed_date_as_cutoff(session, metric_config):
    seed_issuers(session)
    stats = refresh_issuer_metrics(
        session,
        "duke_corp",
        date(2026, 2, 1),
        fetch=fetch_from_fixtures,
        config=metric_config,
    )
    assert stats["issuers"] == 1
    issuer = session.scalar(select(Issuer).where(Issuer.key == "duke_corp"))
    assert issuer is not None
    got = session.scalars(select(CreditMetric).where(CreditMetric.issuer_id == issuer.id)).all()
    assert "FY2025" not in {r.fiscal_label for r in got}  # 10-K not yet filed
    fy_ends = sorted({r.period_end for r in got if r.fiscal_label.startswith("FY")})
    assert max(fy_ends) == date(2024, 12, 31)
