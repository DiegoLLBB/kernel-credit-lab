"""Build a MaturitySchedule wall from 10-K LongTermDebtMaturities* XBRL tags."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.metrics.select import select_instant
from app.onboard.tags import MATURITY_WALL_TAGS, pick_thereafter_tag
from app.sources.xbrl import xbrl_url

# Rest-of-debt note required when no named bonds are seeded.
WALL_NOTE = "rest of the debt reported in the 10-K"


def _bucket(
    *,
    label: str,
    period_end: date | None,
    amount: Decimal,
    tag: str,
) -> dict[str, Any]:
    return {
        "label": label,
        "period_end": period_end.isoformat() if period_end else None,
        "amount": str(amount),
        "source_tag": tag,
        "note": WALL_NOTE,
    }


def _fy_instant(doc: dict[str, Any], tag: str, *, as_of: date):
    point = select_instant(doc, tag, as_of=as_of, fp="FY")
    if point is not None:
        return point
    return select_instant(doc, tag, as_of=as_of)


def build_maturity_buckets(
    doc: dict[str, Any],
    *,
    as_of: date | None = None,
) -> tuple[date, list[dict[str, Any]], str | None]:
    """Return (schedule_as_of, buckets, source_url).

    Uses the latest FY instant of the next-twelve-months tag as the schedule
    as_of (balance-sheet date). Missing year tags are omitted — never estimated.
    """
    cutoff = as_of or date.today()
    from app.onboard.tags import CANDIDATES

    next12_candidates = CANDIDATES.get("mat_next12") or (MATURITY_WALL_TAGS[0][1],)
    anchor = None
    for tag in next12_candidates:
        anchor = _fy_instant(doc, tag, as_of=cutoff)
        if anchor is not None:
            break
    if anchor is None:
        for key, tag in MATURITY_WALL_TAGS[1:]:
            if key == "thereafter":
                continue
            anchor = _fy_instant(doc, tag, as_of=cutoff)
            if anchor is not None:
                break
    if anchor is None:
        raise ValueError(
            "No LongTermDebtMaturities* FY datapoint found; cannot build maturity wall"
        )

    schedule_as_of = anchor.end
    year0 = schedule_as_of.year
    buckets: list[dict[str, Any]] = []

    year_offsets = {
        "next12": 1,
        "year2": 2,
        "year3": 3,
        "year4": 4,
        "year5": 5,
    }

    for key, tag in MATURITY_WALL_TAGS:
        if key == "thereafter":
            continue
        point = _fy_instant(doc, tag, as_of=cutoff)
        if point is None:
            continue
        offset = year_offsets[key]
        try:
            period_end = date(year0 + offset, schedule_as_of.month, schedule_as_of.day)
        except ValueError:
            period_end = date(year0 + offset, 12, 31)
        label = f"FY{period_end.year}"
        buckets.append(_bucket(label=label, period_end=period_end, amount=point.value, tag=tag))

    thereafter = pick_thereafter_tag(doc, as_of=cutoff)
    if thereafter:
        point = _fy_instant(doc, thereafter, as_of=cutoff)
        if point is not None:
            buckets.append(
                _bucket(
                    label=f"after FY{year0 + 5}",
                    period_end=None,
                    amount=point.value,
                    tag=thereafter,
                )
            )

    if not buckets:
        raise ValueError("Maturity tags present but no FY amounts selectable")

    cik = str(doc.get("cik") or "")
    source = xbrl_url(cik) if cik else "https://data.sec.gov/api/xbrl/"
    return schedule_as_of, buckets, source
