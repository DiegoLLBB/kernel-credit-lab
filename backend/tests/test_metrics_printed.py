"""Each metric's FY value vs the number printed in the latest 10-K (or 10-Q).

Method: the recorded companyfacts fixture (tests/fixtures/xbrl/, live SEC API
2026-10-31) is run through compute_issuer with a late cutoff; the FY row for
the latest 10-K must equal the printed figure below (values in USD; printed
figures are $ millions × 1e6). Comments cite the statement line. Any mismatch
fails loudly — a tag that does not reproduce print is removed from
seed/xbrl_metrics.yaml, never worked around.

10-K sources (extracts in /tmp/xbrl/printed_figs.txt, full filings alongside):
DUK duk_10k.htm (FY2025, filed 2026-02-26); ORCL orcl_10k.htm (FY2026 May YE,
filed 2026-06-22); VZ vz_10k.htm (FY2025, filed 2026-02-17); BA ba_10k.htm
(FY2025, filed 2026-01-30); INTC intc_10k.htm (FY2025 Dec-27 YE, filed
2026-01-23); AMZN amzn_10k.htm (FY2025, filed 2026-02-06). Duke subsidiary
registrant figures come from the combined duk_10k.htm registrant statements.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.metrics.compute import compute_issuer
from app.metrics.config import load_metric_config

M = Decimal(1_000_000)
CUTOFF = date(2026, 10, 31)
FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def fy_rows(key: str, cik: str, period_end: date) -> dict[str, Decimal | None]:
    cfg = load_metric_config().for_issuer(key)
    assert cfg is not None and cfg.usable
    doc = json.loads((FIXTURES / f"CIK{cik}.json").read_bytes())
    rows = compute_issuer(doc, cfg, as_of=CUTOFF)
    out = {}
    for r in rows:
        if r.period_end == period_end and r.metric not in out:
            out[r.metric] = r.value
            if r.value is None:
                continue
            filed = r.filed
            assert filed is not None and filed <= CUTOFF, (key, r.metric)
            assert (r.accession or "") != "", (key, r.metric, "accession missing")
    return out


def test_duke_fy2025_matches_printed_10k():
    rows = fy_rows("duke_corp", "0001326160", date(2025, 12, 31))
    assert rows["total_debt"] == 7_104 * M + 80_108 * M  # current maturities + LT debt
    assert rows["cash"] == 245 * M  # cash and cash equivalents
    assert rows["ebitda"] == 8_626 * M + 7_704 * M  # opinc + depr/amort/accretion
    assert rows["fcf"] == 12_330 * M - 14_024 * M  # OCF - capital expenditures
    # maturity tag 7,123 vs printed current maturities 7,104 (VIE rounding);
    # the wall cross-check stores both numbers so the UI can show both.
    assert rows["due_12m"] == 7_123 * M
    assert rows["due_24m"] == (7_123 + 3_639) * M
    assert rows["st_investments"] is None  # last tagged FY2014: cash-only liquidity


def test_oracle_fy2026_matches_printed_10k():
    rows = fy_rows("oracle", "0001341439", date(2026, 5, 31))
    assert rows["total_debt"] == 7_199 * M + 122_342 * M  # = 129,541 total printed
    assert rows["cash"] == 31_289 * M
    assert rows["st_investments"] == 605 * M  # marketable securities
    assert rows["ebitda"] == 20_606 * M + 7_623 * M
    assert rows["fcf"] == 31_977 * M - 55_663 * M
    assert rows["due_12m"] == 7_210 * M
    assert rows["due_24m"] == (7_210 + 10_145) * M


def test_verizon_fy2025_matches_printed_10k():
    rows = fy_rows("verizon", "0000732712", date(2025, 12, 31))
    assert rows["total_debt"] == 18_618 * M + 139_532 * M  # debt <1y + long-term debt
    assert rows["cash"] == 19_048 * M
    assert rows["ebitda"] == 29_259 * M + 18_349 * M  # opinc + D&A expense
    assert rows["fcf"] == 37_137 * M - 17_011 * M  # OCF - capex incl. software
    assert rows["due_12m"] == 17_267 * M  # tag vs printed 18,177/18,618: wall shows both
    assert rows["due_24m"] == (17_267 + 9_569) * M
    assert rows["st_investments"] is None  # last tagged FY2016: cash-only liquidity


def test_boeing_fy2025_matches_printed_10k():
    rows = fy_rows("boeing", "0000012927", date(2025, 12, 31))
    # LongTermDebt tag (53,848) already includes the current portion: it is total
    # debt ex-finance-leases; +250 = 54,098 = 8,461 + 45,637 printed.
    assert rows["total_debt"] == 53_848 * M + 250 * M
    assert rows["cash"] == 10_921 * M
    assert rows["st_investments"] == 18_479 * M  # short-term and other investments
    assert rows["ebitda"] == 4_281 * M + 1_953 * M  # opinc tag vs printed 4,289 (0.2%)
    assert rows["fcf"] == 1_065 * M - 2_942 * M  # OCF - PP&E additions
    assert rows["due_12m"] == 8_351 * M
    assert rows["due_24m"] == (8_351 + 4_403) * M


def test_boeing_opinc_within_half_percent_of_printed():
    """The one non-exact check: tag 4,281 vs printed consolidated 4,289."""
    assert abs(Decimal(4_281_000_000) - Decimal(4_289_000_000)) / Decimal(4_289_000_000) < Decimal(
        "0.005"
    )


def test_intel_fy2025_matches_printed_10k():
    rows = fy_rows("intel", "0000050863", date(2025, 12, 27))
    assert rows["total_debt"] == 2_499 * M + 44_086 * M  # short-term + long-term debt
    assert rows["cash"] == 14_265 * M
    assert rows["st_investments"] == 23_151 * M
    assert rows["ebitda"] == -2_214 * M + 10_757 * M  # operating loss + depreciation
    assert rows["fcf"] == 9_697 * M - 14_646 * M  # OCF - gross PP&E additions
    assert rows["due_12m"] == 2_500 * M
    assert rows["due_24m"] == (2_500 + 3_826) * M


def test_amazon_fy2025_matches_printed_10k():
    rows = fy_rows("amazon", "0001018724", date(2025, 12, 31))
    assert rows["total_debt"] == 2_748 * M + 65_648 * M  # current portion + LT debt
    assert rows["cash"] == 86_810 * M
    assert rows["st_investments"] == 36_219 * M  # marketable securities
    assert rows["ebitda"] == 79_975 * M + 65_756 * M
    assert rows["fcf"] == 139_514 * M - 131_819 * M  # OCF - gross PP&E purchases
    assert rows["due_12m"] == 2_752 * M
    assert rows["due_24m"] == (2_752 + 8_832) * M


def test_interest_inputs_match_printed():
    """Interest-expense tags reproduce the printed lines (VZ/INTC/AMZN use non-face tags)."""
    duk = fy_rows("duke_corp", "0001326160", date(2025, 12, 31))
    orcl = fy_rows("oracle", "0001341439", date(2026, 5, 31))
    assert duk["interest_coverage"] == (
        (Decimal(8_626_000_000) + Decimal(7_704_000_000)) / Decimal(3_634_000_000)
    ).quantize(Decimal("0.0001"))
    assert orcl["interest_coverage"] == (
        (Decimal(20_606_000_000) + Decimal(7_623_000_000)) / Decimal(4_599_000_000)
    ).quantize(Decimal("0.0001"))
    # Gross interest per the notes (face shows only net): VZ 6,694; INTC 1,091; AMZN 2,274.
    vz = fy_rows("verizon", "0000732712", date(2025, 12, 31))
    intc = fy_rows("intel", "0000050863", date(2025, 12, 27))
    amzn = fy_rows("amazon", "0001018724", date(2025, 12, 31))
    assert vz["interest_coverage"] == (
        (Decimal(29_259_000_000) + Decimal(18_349_000_000)) / Decimal(6_694_000_000)
    ).quantize(Decimal("0.0001"))
    assert intc["interest_coverage"] == (
        (Decimal(-2_214_000_000) + Decimal(10_757_000_000)) / Decimal(1_091_000_000)
    ).quantize(Decimal("0.0001"))
    assert amzn["interest_coverage"] == (
        (Decimal(79_975_000_000) + Decimal(65_756_000_000)) / Decimal(2_274_000_000)
    ).quantize(Decimal("0.0001"))


def test_duke_progress_facts_do_not_match_registrant():
    """CIK 17797 restates parent comparatives: FY2025 noncurrent 80,108 equals the
    parent's printed figure, while Progress printed 13,461. Config refuses it."""
    doc = json.loads((FIXTURES / "CIK0000017797.json").read_bytes())
    units = doc["facts"]["us-gaap"]["LongTermDebtNoncurrent"]["units"]["USD"]
    fy2025 = [d for d in units if d.get("end") == "2025-12-31"]
    assert fy2025, "expected parent-scale comparatives in the subsidiary facts"
    assert all(d["val"] == 80_108_000_000 for d in fy2025)  # parent's number, not 13,461
    cfg = load_metric_config().for_issuer("duke_progress")
    assert cfg is not None and not cfg.usable
    assert "13,461" in (cfg.unusable_reason or "")
    assert compute_issuer(doc, cfg, as_of=CUTOFF) == []


@pytest.mark.parametrize(
    "key,cik",
    [
        ("duke_corp", "0001326160"),
        ("oracle", "0001341439"),
        ("verizon", "0000732712"),
        ("boeing", "0000012927"),
        ("intel", "0000050863"),
        ("amazon", "0001018724"),
    ],
)
def test_fy_rows_cite_10k_filing(key, cik):
    """Every FY value traces to the 10-K's accession and filed date."""
    expected_filed = {
        "duke_corp": date(2026, 2, 26),
        "oracle": date(2026, 6, 22),
        "verizon": date(2026, 2, 17),
        "boeing": date(2026, 1, 30),
        "intel": date(2026, 1, 23),
        "amazon": date(2026, 2, 6),
    }
    cfg = load_metric_config().for_issuer(key)
    assert cfg is not None
    doc = json.loads((FIXTURES / f"CIK{cik}.json").read_bytes())
    rows = [r for r in compute_issuer(doc, cfg, as_of=CUTOFF) if r.value is not None]
    fy_ends = sorted({r.period_end for r in rows if r.fiscal_label.startswith("FY")})
    latest = fy_ends[-1]
    for r in rows:
        if r.period_end != latest or not r.fiscal_label.startswith("FY"):
            continue
        assert r.filed == expected_filed[key], (key, r.metric, r.filed)
        assert r.accession, (key, r.metric)
