"""Point-in-time datapoint selection from a companyfacts document.

Visibility rule: a datapoint is visible only from its ``filed`` date — every
selector takes ``as_of`` and ignores anything filed later, so replay and the
date slider can never see the future. Exact period-end matching doubles as the
staleness guard: an abandoned tag (e.g. Verizon's ShortTermInvestments, last
tagged FY2016) simply has no datapoint for recent periods.

Flows use trailing-twelve-months: latest full fiscal year + latest year-to-date
minus the same year-to-date a year earlier, matched by fiscal period (fp),
fiscal year (fy) and duration length in months. Quarter and YTD facts in one
10-Q share fp/fy and differ only by duration (Q2: 3 vs 6 months), so duration
matching is what keeps a 3-month quarter from masquerading as 6-month YTD.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

QUARTER_MONTHS = {"Q1": 3, "Q2": 6, "Q3": 9}
AVG_MONTH_DAYS = 30.4375


@dataclass(frozen=True)
class Datapoint:
    tag: str
    value: Decimal
    end: date
    start: date | None
    filed: date
    accession: str
    fy: int | None
    fp: str
    form: str
    unit: str

    @property
    def months(self) -> int | None:
        if self.start is None:
            return None
        return round((self.end - self.start).days / AVG_MONTH_DAYS)


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_fy(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def iter_datapoints(
    doc: dict[str, Any], tag: str, *, unit: str = "USD", as_of: date | None = None
) -> list[Datapoint]:
    """All datapoints for a us-gaap tag in ``unit``, optionally filed ≤ ``as_of``."""
    try:
        units = doc["facts"]["us-gaap"][tag]["units"]
    except (KeyError, TypeError):
        return []
    out: list[Datapoint] = []
    for unit_name, entries in (units or {}).items():
        if unit_name != unit:
            continue
        for entry in entries or []:
            filed = _parse_date(entry.get("filed"))
            end = _parse_date(entry.get("end"))
            if filed is None or end is None:
                continue
            if as_of is not None and filed > as_of:
                continue
            try:
                value = Decimal(str(entry.get("val")))
            except Exception:
                continue
            out.append(
                Datapoint(
                    tag=tag,
                    value=value,
                    end=end,
                    start=_parse_date(entry.get("start")),
                    filed=filed,
                    accession=str(entry.get("accn") or ""),
                    fy=_parse_fy(entry.get("fy")),
                    fp=str(entry.get("fp") or ""),
                    form=str(entry.get("form") or ""),
                    unit=unit_name,
                )
            )
    return out


def select_instant(
    doc: dict[str, Any],
    tag: str,
    *,
    as_of: date,
    period_end: date | None = None,
    fp: str | None = None,
) -> Datapoint | None:
    """Point-in-time balance-sheet fact: latest filing for the period end.

    With ``period_end``: the datapoint with that exact end and the latest filed
    date (amendments win). Without: the latest end, breaking ties by filed.
    ``fp`` restricts to a fiscal period (``"FY"`` keeps FY rows on the 10-K when
    a later 10-Q restates the same year-end balance sheet).
    """
    cands = [d for d in iter_datapoints(doc, tag, as_of=as_of) if d.start is None]
    if period_end is not None:
        cands = [d for d in cands if d.end == period_end]
    if fp is not None:
        cands = [d for d in cands if d.fp == fp]
    if not cands:
        return None
    cands.sort(key=lambda d: (d.end, d.filed))
    return cands[-1]


def select_full_year(
    doc: dict[str, Any], tag: str, *, as_of: date, period_end: date | None = None
) -> Datapoint | None:
    """Full-fiscal-year flow fact (fp == FY), latest filing for the period end."""
    cands = [
        d for d in iter_datapoints(doc, tag, as_of=as_of) if d.fp == "FY" and d.start is not None
    ]
    if period_end is not None:
        cands = [d for d in cands if d.end == period_end]
    if not cands:
        return None
    cands.sort(key=lambda d: (d.end, d.filed))
    return cands[-1]


@dataclass(frozen=True)
class TtmResult:
    value: Decimal
    full_year: Datapoint
    ytd: Datapoint | None
    prior_ytd: Datapoint | None

    @property
    def is_full_year_only(self) -> bool:
        return self.ytd is None


def trailing_twelve_months(doc: dict[str, Any], tag: str, *, as_of: date) -> TtmResult | None:
    """TTM flow = latest FY + latest YTD − same YTD a year earlier.

    Matching is by fiscal period (fp) and duration length, anchored on period
    *ends* — never on the ``fy`` attribute, whose convention varies by filer
    (Oracle's Q1 FY2027 quarter facts carry fy=2026). The prior-year leg must
    end 340–390 days before the current YTD; anything older means the
    same-period comparison is missing, and returning None keeps a stale year
    from silently inflating the trend.

    Returns a full-year-only result when no quarter has been filed since the
    10-K (the caller then reuses the FY row instead of emitting a quarter row).
    """
    fys = [
        d for d in iter_datapoints(doc, tag, as_of=as_of) if d.fp == "FY" and d.start is not None
    ]
    if not fys:
        return None
    fys.sort(key=lambda d: (d.end, d.filed))
    full = fys[-1]

    def is_ytd(d: Datapoint, quarter: str) -> bool:
        return d.fp == quarter and d.start is not None and d.months == QUARTER_MONTHS[quarter]

    def ytd_after(quarter: str) -> Datapoint | None:
        cands = [
            d
            for d in iter_datapoints(doc, tag, as_of=as_of)
            if is_ytd(d, quarter) and d.end > full.end and d.filed > full.filed
        ]
        if not cands:
            return None
        cands.sort(key=lambda d: (d.end, d.filed))
        return cands[-1]

    def prior_ytd(current: Datapoint, quarter: str) -> Datapoint | None:
        cands = [
            d
            for d in iter_datapoints(doc, tag, as_of=as_of)
            if is_ytd(d, quarter) and d.end < full.end and 340 <= (current.end - d.end).days <= 390
        ]
        if not cands:
            return None
        cands.sort(key=lambda d: (d.end, d.filed))
        return cands[-1]

    for quarter in ("Q3", "Q2", "Q1"):
        ytd = ytd_after(quarter)
        if ytd is None:
            continue
        prior = prior_ytd(ytd, quarter)
        if prior is None:
            return None
        return TtmResult(
            value=full.value + ytd.value - prior.value,
            full_year=full,
            ytd=ytd,
            prior_ytd=prior,
        )
    return TtmResult(value=full.value, full_year=full, ytd=None, prior_ytd=None)


def full_year_ends(doc: dict[str, Any], tag: str, *, as_of: date, limit: int) -> list[date]:
    """Distinct FY period ends with data, newest ``limit`` (sparkline history)."""
    ends = {
        d.end
        for d in iter_datapoints(doc, tag, as_of=as_of)
        if d.fp == "FY" and d.start is not None
    } | {d.end for d in iter_datapoints(doc, tag, as_of=as_of) if d.fp == "FY" and d.start is None}
    return sorted(ends)[-limit:]
