"""Compare verified chart readings to SEC XBRL / Kernel credit metrics.

Only charts whose title+unit match a configured metric family are checked.
Each category→value is scored agree (≤1%), disagree (both values shown), or
not comparable (with reason). Disagreements are never suppressed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChartReading, CreditMetric, Issuer

# Relative tolerance for "agree".
AGREE_TOLERANCE = Decimal("0.01")

# Configured metric families: match by title/unit keywords, never by guessing.
# Prefer credit_metric rows when present; else look up us-gaap tags in companyfacts.
METRIC_FAMILIES: list[dict[str, Any]] = [
    {
        "id": "total_debt",
        "label": "total / consolidated debt",
        "title_all": [],  # any of title_any
        "title_any": [
            "consolidated debt",
            "total debt",
            "long-term debt",
            "long term debt",
            "debt outstanding",
            "debt",
        ],
        "title_exclude": [
            "net debt",
            "debt/ebitda",
            "debt to",
            "leverage",
            "maturity",
            "due by",
            "profile",
            "interest",
        ],
        "unit_any": ["$", "usd", "million", "billion", "m", "b", "$m", "$b", "$mm", "$bn"],
        "credit_metric": "total_debt",
        "xbrl_tags": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
        "instant": True,
    },
    {
        "id": "cash",
        "label": "cash",
        "title_any": [
            "cash and cash equivalents",
            "cash & cash equivalents",
            "cash and equivalents",
            "cash balance",
            "ending cash",
        ],
        "title_exclude": ["free cash", "operating cash", "cash flow"],
        "unit_any": ["$", "usd", "million", "billion", "m", "b", "$m", "$b"],
        "credit_metric": "cash",
        "xbrl_tags": ["CashAndCashEquivalentsAtCarryingValue"],
        "instant": True,
    },
    {
        "id": "revenue",
        "label": "revenue / net sales",
        "title_any": ["net sales", "total revenue", "revenues", "net revenue", "sales"],
        "title_exclude": ["same-store", "operating income", "eps"],
        "unit_any": ["$", "usd", "million", "billion", "m", "b", "$m", "$b"],
        "credit_metric": None,
        "xbrl_tags": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        "instant": False,
    },
    {
        "id": "operating_income",
        "label": "operating income",
        "title_any": [
            "operating income",
            "income from operations",
            "earnings from operations",
            "operating profit",
            "opinc",
        ],
        "title_exclude": ["net income", "eps"],
        "unit_any": ["$", "usd", "million", "billion", "m", "b", "$m", "$b"],
        "credit_metric": None,
        "xbrl_tags": ["OperatingIncomeLoss"],
        "instant": False,
    },
    {
        "id": "eps",
        "label": "earnings per share",
        "title_any": ["diluted eps", "eps diluted", "earnings per share", "diluted earnings"],
        "title_exclude": ["adjusted eps", "non-gaap"],
        "unit_any": ["$", "usd", "per share", "/share", "eps"],
        "credit_metric": None,
        "xbrl_tags": ["EarningsPerShareDiluted"],
        "instant": False,
        "per_share": True,
    },
]


@dataclass
class ValueCrosscheck:
    category: str
    chart_value: float | None
    chart_unit: str | None
    status: str  # agree | disagree | not_comparable | skipped
    reason: str | None = None
    xbrl_value: float | None = None
    xbrl_unit: str | None = None
    fiscal_label: str | None = None
    period_end: str | None = None
    accession: str | None = None
    filing_url: str | None = None
    metric_id: str | None = None
    metric_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChartCrosscheck:
    chart_id: str
    title: str | None
    unit: str | None
    metric_id: str | None
    metric_label: str | None
    summary_status: str  # agree | differ | not_comparable | not_applicable
    values: list[ValueCrosscheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "title": self.title,
            "unit": self.unit,
            "metric_id": self.metric_id,
            "metric_label": self.metric_label,
            "summary_status": self.summary_status,
            "values": [v.to_dict() for v in self.values],
        }


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _unit_matches(chart_unit: str | None, unit_any: list[str]) -> bool:
    u = _norm(chart_unit)
    if not u:
        # Missing unit: allow dollar families only when title clearly monetary.
        return False
    for tok in unit_any:
        if _norm(tok) in u or u == _norm(tok):
            return True
    # Bare "$B" / "$M" style
    if re.search(r"\b[mb]\b", u) or "$" in (chart_unit or ""):
        return any(t in {"$", "usd", "m", "b", "$m", "$b"} for t in unit_any)
    return False


def match_metric_family(title: str | None, unit: str | None) -> dict[str, Any] | None:
    """Return the configured family for this chart title+unit, or None."""
    t = _norm(title)
    if not t:
        return None
    for fam in METRIC_FAMILIES:
        if any(ex in t for ex in fam.get("title_exclude") or []):
            continue
        if not any(k in t for k in fam["title_any"]):
            continue
        # EPS charts often omit "$" in unit; allow per-share units or empty with eps in title.
        if fam.get("per_share"):
            u = _norm(unit)
            unit_ok = _unit_matches(unit, fam["unit_any"])
            if u and not unit_ok and "share" not in u and "eps" not in u:
                continue
        elif not _unit_matches(unit, fam["unit_any"]):
            # Title strong enough + missing unit → still try for debt/cash/revenue.
            if unit and str(unit).strip():
                continue
        return fam
    return None


def parse_category_period(label: Any) -> dict[str, Any] | None:
    """Parse chart category labels like 3Q26, Q3 2026, FY2025, 2025."""
    if label is None:
        return None
    text = str(label).strip()
    if not text:
        return None

    # FY2025 / FY25 / 2025
    m = re.match(r"^(?:FY\s*)?((?:19|20)\d{2})$", text, re.I)
    if m:
        year = int(m.group(1))
        return {"kind": "fy", "year": year, "fiscal_label": f"FY{year}"}
    m = re.match(r"^FY\s*'?(\d{2})$", text, re.I)
    if m:
        year = 2000 + int(m.group(1))
        return {"kind": "fy", "year": year, "fiscal_label": f"FY{year}"}

    # 3Q26 / 3Q 2026 / Q3 2026 / Q3'26 / 2026 Q3
    m = re.match(
        r"^(?:Q\s*)?([1-4])\s*Q\s*'?((?:19|20)\d{2}|\d{2})$|"
        r"^Q\s*([1-4])\s*'?((?:19|20)\d{2}|\d{2})$|"
        r"^((?:19|20)\d{2})\s*Q\s*([1-4])$",
        text,
        re.I,
    )
    if not m:
        m = re.match(r"^([1-4])Q\s*'?((?:19|20)\d{2}|\d{2})$", text, re.I)
    if m:
        groups = [g for g in m.groups() if g is not None]
        if len(groups) >= 2:
            if groups[0] in {"1", "2", "3", "4"}:
                q, ys = int(groups[0]), groups[1]
            else:
                ys, q = groups[0], int(groups[1])
            year = int(ys) if len(ys) == 4 else 2000 + int(ys)
            return {
                "kind": "quarter",
                "year": year,
                "quarter": q,
                "fiscal_label": f"Q{q} {year}",
                "fp": f"Q{q}",
            }
    return None


def chart_value_to_usd(value: Any, unit: str | None, *, per_share: bool = False) -> Decimal | None:
    try:
        n = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if per_share:
        return n
    u = _norm(unit)
    compact = (unit or "").lower().replace(" ", "")
    if "billion" in u or re.search(r"\bb(?:n|illions?)?\b", u) or compact in {"$b", "$bn", "usdb"}:
        return n * Decimal("1000000000")
    if "million" in u or re.search(r"\bm(?:m|illions?)?\b", u) or compact in {"$m", "$mm", "usdm"}:
        return n * Decimal("1000000")
    # Already absolute USD (large face amount).
    if abs(n) >= Decimal("1000000"):
        return n
    # Scale not stated — caller treats as not comparable.
    return None


def _within_tol(a: Decimal, b: Decimal, tol: Decimal = AGREE_TOLERANCE) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= tol


def accession_url(cik: str | None, accession: str | None) -> str | None:
    if not cik or not accession:
        return None
    cik_num = str(cik).lstrip("0") or "0"
    accn = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{accn}/{accession}-index.htm"
    )


def _duration_days(pt: dict[str, Any]) -> int | None:
    start, end = pt.get("start"), pt.get("end")
    if not start or not end:
        return None
    try:
        s = date.fromisoformat(str(start)[:10])
        e = date.fromisoformat(str(end)[:10])
    except ValueError:
        return None
    return (e - s).days


def _pick_xbrl_point(
    facts: dict[str, Any],
    tags: list[str],
    period: dict[str, Any],
    *,
    instant: bool,
    per_share: bool = False,
) -> dict[str, Any] | None:
    """Pick the best companyfacts datapoint for a parsed period."""
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    candidates: list[dict[str, Any]] = []
    for tag in tags:
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units") or {}
        unit_keys = list(units.keys())
        preferred = []
        if per_share:
            preferred = [u for u in unit_keys if "share" in u.lower()]
        else:
            preferred = [u for u in unit_keys if u.upper() == "USD"] or unit_keys
        for uk in preferred:
            for pt in units.get(uk) or []:
                if pt.get("val") is None:
                    continue
                form = (pt.get("form") or "").upper()
                if form not in {"10-K", "10-Q", "20-F", "40-F"}:
                    continue
                candidates.append({**pt, "_tag": tag, "_unit": uk})

    if not candidates:
        return None

    year = period.get("year")
    fp = period.get("fp")
    kind = period.get("kind")

    def score(pt: dict[str, Any]) -> tuple:
        end = str(pt.get("end") or "")
        fy = pt.get("fy")
        pt_fp = (pt.get("fp") or "").upper()
        dur = _duration_days(pt)
        # Prefer matching fy/fp; for quarters prefer ~90-day duration over YTD.
        match = 0
        dur_pref = 0
        if kind == "fy":
            if pt_fp in {"FY", ""} or (dur and dur >= 300):
                match += 5
            if fy == year or (
                end.startswith(str(year)) and (not pt.get("start") or (dur and dur >= 300))
            ):
                match += 5
            if instant and not pt.get("start"):
                match += 2
            if dur is not None:
                dur_pref = -abs(dur - 365)
        else:
            if pt_fp == fp:
                match += 6
            # Prefer the period whose end year matches the chart label (not the
            # comparative prior-year quarter that shares the same FY/fp in a 10-Q).
            if end.startswith(str(year)):
                match += 8
            elif fy == year:
                match += 2
            if not instant and dur is not None and 60 <= dur <= 120:
                match += 8  # quarterly duration strongly preferred
            if not instant and dur is not None and dur >= 240:
                match -= 6  # YTD / nine-month
            if instant and not pt.get("start"):
                match += 2
            if dur is not None:
                dur_pref = -abs(dur - 90)
        filed = str(pt.get("filed") or "")
        return (match, dur_pref, filed)

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0]
    best_score = score(best)[0]
    if best_score < 5:
        return None
    # Ambiguous only when top match+duration still disagree on value.
    top = score(best)[:2]
    ties = [p for p in ranked if score(p)[:2] == top]
    vals = {Decimal(str(p["val"])) for p in ties}
    if len(vals) > 1:
        return {"ambiguous": True, "points": ties}
    return best


def _credit_metric_for_period(
    session: Session,
    issuer_id: str,
    metric: str,
    period: dict[str, Any],
) -> CreditMetric | None:
    rows = session.scalars(
        select(CreditMetric)
        .where(CreditMetric.issuer_id == issuer_id, CreditMetric.metric == metric)
        .order_by(CreditMetric.filed.desc().nullslast(), CreditMetric.period_end.desc())
    ).all()
    if not rows:
        return None
    label = period.get("fiscal_label")
    year = period.get("year")
    kind = period.get("kind")
    for row in rows:
        if row.value is None:
            continue
        if label and row.fiscal_label == label:
            return row
        if kind == "fy" and row.fiscal_label == f"FY{year}":
            return row
        if kind == "quarter" and row.fiscal_label == label:
            return row
    # Soft match: period_end year + quarter month
    if kind == "quarter" and year and period.get("quarter"):
        q = period["quarter"]
        # Approximate quarter-end months
        month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        for row in rows:
            if row.value is None:
                continue
            if row.period_end.year == year and row.period_end.month == month:
                return row
    if kind == "fy" and year:
        for row in rows:
            if row.value is None:
                continue
            if row.fiscal_label.startswith("FY") and row.period_end.year == year:
                return row
    return None


def crosscheck_chart(
    session: Session,
    chart: ChartReading,
    *,
    issuer: Issuer | None = None,
    companyfacts: dict[str, Any] | None = None,
) -> ChartCrosscheck:
    """Cross-check one verified chart against filings / credit metrics."""
    fam = match_metric_family(chart.title, chart.unit)
    if fam is None:
        return ChartCrosscheck(
            chart_id=chart.id,
            title=chart.title,
            unit=chart.unit,
            metric_id=None,
            metric_label=None,
            summary_status="not_applicable",
            values=[
                ValueCrosscheck(
                    category="*",
                    chart_value=None,
                    chart_unit=chart.unit,
                    status="skipped",
                    reason="title/unit do not match a configured filing metric",
                )
            ],
        )

    if issuer is None and chart.issuer_id:
        issuer = session.get(Issuer, chart.issuer_id)

    results: list[ValueCrosscheck] = []
    cats = list(chart.categories or [])
    vals = list(chart.values or [])
    n = max(len(cats), len(vals))
    for i in range(n):
        cat = cats[i] if i < len(cats) else f"#{i}"
        raw = vals[i] if i < len(vals) else None
        if raw is None:
            results.append(
                ValueCrosscheck(
                    category=str(cat),
                    chart_value=None,
                    chart_unit=chart.unit,
                    status="not_comparable",
                    reason="chart value missing",
                    metric_id=fam["id"],
                    metric_label=fam["label"],
                )
            )
            continue
        period = parse_category_period(cat)
        if period is None:
            results.append(
                ValueCrosscheck(
                    category=str(cat),
                    chart_value=float(raw) if _is_number(raw) else None,
                    chart_unit=chart.unit,
                    status="not_comparable",
                    reason="period unclear from category label",
                    metric_id=fam["id"],
                    metric_label=fam["label"],
                )
            )
            continue

        chart_usd = chart_value_to_usd(raw, chart.unit, per_share=bool(fam.get("per_share")))
        if chart_usd is None:
            results.append(
                ValueCrosscheck(
                    category=str(cat),
                    chart_value=float(raw) if _is_number(raw) else None,
                    chart_unit=chart.unit,
                    status="not_comparable",
                    reason="chart unit scale unclear (need $M / $B) or value not numeric",
                    fiscal_label=period.get("fiscal_label"),
                    metric_id=fam["id"],
                    metric_label=fam["label"],
                )
            )
            continue

        xbrl_val: Decimal | None = None
        accession: str | None = None
        fiscal_label = period.get("fiscal_label")
        period_end: str | None = None
        xbrl_unit = "USD/shares" if fam.get("per_share") else "USD"
        filing_url: str | None = None

        # 1) Credit metric table
        if fam.get("credit_metric") and issuer is not None:
            row = _credit_metric_for_period(session, issuer.id, fam["credit_metric"], period)
            if row is not None and row.value is not None:
                xbrl_val = Decimal(str(row.value))
                accession = row.accession
                fiscal_label = row.fiscal_label or fiscal_label
                period_end = row.period_end.isoformat() if row.period_end else None
                filing_url = accession_url(issuer.cik, accession)

        # 2) Companyfacts fallback
        if xbrl_val is None and companyfacts is not None:
            picked = _pick_xbrl_point(
                companyfacts,
                list(fam.get("xbrl_tags") or []),
                period,
                instant=bool(fam.get("instant")),
                per_share=bool(fam.get("per_share")),
            )
            if picked and picked.get("ambiguous"):
                results.append(
                    ValueCrosscheck(
                        category=str(cat),
                        chart_value=float(chart_usd),
                        chart_unit=chart.unit,
                        status="not_comparable",
                        reason="ambiguous metric: multiple XBRL values for this period",
                        fiscal_label=fiscal_label,
                        metric_id=fam["id"],
                        metric_label=fam["label"],
                    )
                )
                continue
            if picked:
                xbrl_val = Decimal(str(picked["val"]))
                accession = picked.get("accn")
                period_end = str(picked.get("end") or "")[:10] or None
                xbrl_unit = picked.get("_unit") or xbrl_unit
                filing_url = accession_url(issuer.cik if issuer else None, accession)

        if xbrl_val is None:
            results.append(
                ValueCrosscheck(
                    category=str(cat),
                    chart_value=float(chart_usd),
                    chart_unit=chart.unit,
                    status="not_comparable",
                    reason="no XBRL value for this period",
                    fiscal_label=fiscal_label,
                    period_end=period_end,
                    metric_id=fam["id"],
                    metric_label=fam["label"],
                )
            )
            continue

        agree = _within_tol(chart_usd, xbrl_val)
        results.append(
            ValueCrosscheck(
                category=str(cat),
                chart_value=float(chart_usd),
                chart_unit=chart.unit,
                status="agree" if agree else "disagree",
                reason=None if agree else "chart value differs from XBRL by more than 1%",
                xbrl_value=float(xbrl_val),
                xbrl_unit=xbrl_unit,
                fiscal_label=fiscal_label,
                period_end=period_end,
                accession=accession,
                filing_url=filing_url,
                metric_id=fam["id"],
                metric_label=fam["label"],
            )
        )

    statuses = {r.status for r in results if r.status != "skipped"}
    if "disagree" in statuses:
        summary = "differ"
    elif statuses and statuses <= {"agree"}:
        summary = "agree"
    else:
        summary = "not_comparable"

    return ChartCrosscheck(
        chart_id=chart.id,
        title=chart.title,
        unit=chart.unit,
        metric_id=fam["id"],
        metric_label=fam["label"],
        summary_status=summary,
        values=results,
    )


def _is_number(raw: Any) -> bool:
    try:
        float(str(raw).replace(",", "").replace("$", ""))
        return True
    except (TypeError, ValueError):
        return False


def crosscheck_verified_charts(
    session: Session,
    *,
    issuer_id: str | None = None,
    companyfacts_by_cik: dict[str, dict[str, Any]] | None = None,
) -> list[ChartCrosscheck]:
    """Cross-check all verified charts (optionally for one issuer)."""
    q = select(ChartReading).where(ChartReading.verified.is_(True))
    if issuer_id:
        q = q.where(ChartReading.issuer_id == issuer_id)
    rows = session.scalars(q.order_by(ChartReading.created_at.asc())).all()
    out: list[ChartCrosscheck] = []
    for row in rows:
        issuer = session.get(Issuer, row.issuer_id) if row.issuer_id else None
        facts = None
        if companyfacts_by_cik and issuer and issuer.cik:
            facts = companyfacts_by_cik.get(issuer.cik) or companyfacts_by_cik.get(
                issuer.cik.lstrip("0")
            )
        out.append(crosscheck_chart(session, row, issuer=issuer, companyfacts=facts))
    return out


def summarize_crosschecks(results: list[ChartCrosscheck]) -> dict[str, int]:
    """Count chart-level outcomes for applicable charts only."""
    agree = differ = not_comp = 0
    for r in results:
        if r.summary_status == "not_applicable":
            continue
        if r.summary_status == "agree":
            agree += 1
        elif r.summary_status == "differ":
            differ += 1
        else:
            not_comp += 1
    return {"agree": agree, "differ": differ, "not_comparable": not_comp}
