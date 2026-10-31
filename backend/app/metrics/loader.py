"""Idempotent credit-metric loading: fixtures/live facts → credit_metric rows.

``load_metrics`` fetches each configured issuer's companyfacts (via the passed
``fetch`` callable so tests inject fixtures and the seed job injects the live
SEC client), computes rows with :func:`app.metrics.compute.compute_issuer`,
and upserts on (issuer, metric, period_end). Re-running changes nothing unless
the inputs changed; corrections arrive as updated rows, never duplicates.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.metrics.compute import MetricRow, compute_issuer
from app.metrics.config import MetricsConfig, load_metric_config
from app.models.credit import CreditMetric, Issuer, MaturitySchedule

FetchFacts = Callable[[str], dict[str, Any]]


def wall_for_issuer(session: Any, issuer_id: str) -> dict[date, tuple[Decimal, Decimal]]:
    """Map metric period end → (wall due_12m, wall due_24m) from stored schedules.

    A schedule ``as_of == E`` holds buckets labelled by the fiscal year they
    fall in, so debt maturing in the next 12/24 months after E is the E+1y
    bucket (plus E+2y). Only schedules whose buckets carry ``period_end`` dates
    participate; anything else is skipped rather than guessed.
    """
    out: dict[date, tuple[Decimal, Decimal]] = {}
    rows = session.scalars(
        select(MaturitySchedule).where(MaturitySchedule.issuer_id == issuer_id)
    ).all()
    for sched in rows:
        as_of = sched.as_of
        yearly: dict[date, Decimal] = {}
        for bucket in sched.buckets or []:
            try:
                end = date.fromisoformat(str(bucket.get("period_end"))[:10])
                amount = Decimal(str(bucket.get("amount")))
            except Exception:
                continue
            yearly[end] = amount
        try:
            first = yearly.get(date(as_of.year + 1, as_of.month, as_of.day))
            second = yearly.get(date(as_of.year + 2, as_of.month, as_of.day))
        except ValueError:
            continue
        if first is None:
            continue
        out[as_of] = (first, first + (second or Decimal(0)))
    return out


def upsert_rows(session: Any, issuer_id: str, rows: Iterable[MetricRow]) -> tuple[int, int]:
    """Upsert metric rows; returns (created, updated)."""
    created = updated = 0
    for row in rows:
        existing = session.scalar(
            select(CreditMetric).where(
                CreditMetric.issuer_id == issuer_id,
                CreditMetric.metric == row.metric,
                CreditMetric.period_end == row.period_end,
            )
        )
        if existing is None:
            session.add(
                CreditMetric(
                    issuer_id=issuer_id,
                    metric=row.metric,
                    period_end=row.period_end,
                    fiscal_label=row.fiscal_label,
                    value=row.value,
                    unit=row.unit,
                    source_tag=row.source_tag or None,
                    accession=row.accession,
                    filed=row.filed,
                    detail=row.detail,
                )
            )
            created += 1
            continue
        changed = (
            existing.value != row.value
            or existing.fiscal_label != row.fiscal_label
            or (existing.source_tag or "") != (row.source_tag or "")
            or existing.accession != row.accession
            or existing.filed != row.filed
            or (existing.detail or {}) != (row.detail or {})
        )
        if changed:
            existing.fiscal_label = row.fiscal_label
            existing.value = row.value
            existing.unit = row.unit
            existing.source_tag = row.source_tag or None
            existing.accession = row.accession
            existing.filed = row.filed
            existing.detail = row.detail
            updated += 1
    return created, updated


def load_metrics(
    session: Any,
    fetch: FetchFacts,
    *,
    config: MetricsConfig | None = None,
    config_path: Any = None,
    as_of: date | None = None,
    issuer_keys: Iterable[str] | None = None,
    with_wall: bool = True,
) -> dict[str, int]:
    """Load metrics for configured issuers; returns stats.

    ``as_of`` is the point-in-time cutoff (datapoints filed later are
    invisible); defaults to today. ``issuer_keys`` restricts the run (used by
    the replay refresh for a single issuer).
    """
    cfg = config or load_metric_config(config_path)
    cutoff = as_of or date.today()
    stats = {"issuers": 0, "rows_created": 0, "rows_updated": 0, "skipped": 0}
    wanted = set(issuer_keys) if issuer_keys is not None else None
    if wanted is not None:
        stats["skipped"] += sum(1 for key in wanted if key not in cfg.issuers)
    for key, issuer_cfg in cfg.issuers.items():
        if wanted is not None and key not in wanted:
            continue
        issuer = session.scalar(select(Issuer).where(Issuer.key == key))
        if issuer is None:
            stats["skipped"] += 1
            continue
        if not issuer_cfg.usable:
            stats["skipped"] += 1
            continue
        doc = fetch(issuer_cfg.cik)
        wall = wall_for_issuer(session, issuer.id) if with_wall else {}
        rows = compute_issuer(doc, issuer_cfg, as_of=cutoff, wall=wall or None)
        created, updated = upsert_rows(session, issuer.id, rows)
        stats["issuers"] += 1
        stats["rows_created"] += created
        stats["rows_updated"] += updated
    return stats
