"""Sector peer median and rank for credit metric tiles."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreditMetric, Issuer

# prior_same_period unused here — peer values are latest-as-of only.

BANK_NA_METRICS = frozenset(
    {
        "ebitda",
        "leverage",
        "net_leverage",
        "interest_coverage",
        "fcf",
        "fcf_to_debt",
    }
)
BANK_NA_REASON = "not meaningful for banks"


def is_bank_sector(sector: str | None) -> bool:
    return (sector or "").strip() in {"Financials", "Banks"}


def _ordinal(n: int) -> str:
    suffix = (
        "th"
        if 10 <= n % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def _latest_value(
    session: Session, issuer_id: str, metric: str, as_of: date
) -> Decimal | None:
    rows = list(
        session.scalars(
            select(CreditMetric)
            .where(
                CreditMetric.issuer_id == issuer_id,
                CreditMetric.metric == metric,
                CreditMetric.filed <= as_of,
                CreditMetric.value.is_not(None),
            )
            .order_by(CreditMetric.period_end.asc())
        ).all()
    )
    if not rows:
        return None
    return rows[-1].value


def peer_comparison(
    session: Session,
    issuer: Issuer,
    metric: str,
    as_of: date,
    *,
    value: Decimal | None,
) -> dict[str, Any] | None:
    """Median and rank among same-sector root issuers with a value for ``metric``.

    Returns None when this issuer has no value. When the sector has fewer than
    three peers with data, ``note`` explains that and median/rank stay null.
    """
    if value is None:
        return None
    sector = (issuer.sector or "").strip() or None
    if not sector:
        return {
            "sector": None,
            "n": 1,
            "median": None,
            "rank": 1,
            "ordinal": "1st",
            "note": "no sector set — peer comparison unavailable",
        }
    peers = session.scalars(
        select(Issuer).where(
            Issuer.parent_id.is_(None),
            Issuer.sector == sector,
        )
    ).all()
    scored: list[tuple[str, Decimal]] = []
    for peer in peers:
        if not peer.id:
            continue
        if is_bank_sector(peer.sector) and metric in BANK_NA_METRICS:
            continue
        v = _latest_value(session, peer.id, metric, as_of)
        if v is None:
            continue
        scored.append((peer.id, v))
    n = len(scored)
    if n < 3:
        return {
            "sector": sector,
            "n": n,
            "median": None,
            "rank": None,
            "ordinal": None,
            "note": f"sector has fewer than 3 companies with this metric ({n})",
        }
    # Higher leverage / net leverage is worse; for coverage / fcf ratios higher is better.
    # Rank 1 = strongest: lowest leverage, highest coverage.
    ascending = metric in {"leverage", "net_leverage", "due_12m", "due_24m", "total_debt"}
    ordered = sorted(scored, key=lambda t: float(t[1]), reverse=not ascending)
    rank = next((i + 1 for i, (pid, _) in enumerate(ordered) if pid == issuer.id), None)
    values = sorted(float(v) for _pid, v in scored)
    mid = len(values) // 2
    if len(values) % 2:
        median = Decimal(str(values[mid]))
    else:
        median = Decimal(str((values[mid - 1] + values[mid]) / 2))
    return {
        "sector": sector,
        "n": n,
        "median": median,
        "rank": rank,
        "ordinal": _ordinal(rank) if rank else None,
        "note": None,
    }


def sector_label_for_display(sector: str | None) -> str:
    if not sector:
        return "peers"
    return sector.lower()
