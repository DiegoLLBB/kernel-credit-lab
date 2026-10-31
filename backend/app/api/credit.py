"""Analyst-layer read API: "What changed" overview and shared tile builders.

Plain-English endpoints over credit_metric rows, maturity walls and checked
insight text. Every number comes from the database; missing data is reported
with its reason, never filled in.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.alerts import _applied_changes, _issuer_ref, impact_for_alert
from app.api.common import clamp_limit
from app.api.schemas import (
    BeforeAfterOut,
    CreditOverviewOut,
    EvalSummaryOut,
    FeedItemOut,
    MetricTileOut,
    OverviewHeaderOut,
    SparkPoint,
    ThresholdBandOut,
    WatchlistRowOut,
)
from app.db import get_session
from app.metrics.compute import METRIC_IDS
from app.metrics.config import load_metric_config
from app.models import Alert, CreditMetric, Event, Filing, Issuer, ReplayEvaluation

router = APIRouter(prefix="/api/credit", tags=["credit"])
DbSession = Annotated[Session, Depends(get_session)]

METRIC_LABELS = {
    "total_debt": "Total debt",
    "cash": "Cash",
    "st_investments": "Short-term investments",
    "due_12m": "Debt due in 12 months",
    "due_24m": "Debt due in 24 months",
    "liquidity_coverage": "Cash ÷ debt due in 24 months",
    "ebitda": "EBITDA",
    "leverage": "Debt ÷ EBITDA",
    "net_leverage": "Net debt ÷ EBITDA",
    "interest_coverage": "EBITDA ÷ interest",
    "fcf": "Free cash flow",
    "fcf_to_debt": "Free cash flow ÷ debt",
}

_thresholds_cache: dict[str, Any] | None = None


def load_thresholds() -> dict[str, Any]:
    """Flag bands from seed/metric_thresholds.yaml (shown in the UI legend)."""
    global _thresholds_cache
    if _thresholds_cache is None:
        here = Path(__file__).resolve()
        candidate = here.parents[2] / "seed" / "metric_thresholds.yaml"
        _thresholds_cache = yaml.safe_load(candidate.read_text()) or {}
    return _thresholds_cache


def flag_for(metric: str, value: Decimal | None) -> str | None:
    """Green/amber/red screening flag, None where no band applies."""
    if value is None:
        return None
    bands = (load_thresholds().get("metrics") or {}).get(metric)
    if not bands:
        return None
    if "green_max" in bands:
        if value <= Decimal(str(bands["green_max"])):
            return "green"
        return "amber" if value <= Decimal(str(bands["amber_max"])) else "red"
    if value >= Decimal(str(bands["green_min"])):
        return "green"
    return "amber" if value >= Decimal(str(bands["amber_min"])) else "red"


def threshold_legend() -> list[ThresholdBandOut]:
    return [
        ThresholdBandOut(
            label=cfg.get("label", metric),
            unit=cfg.get("unit", "x"),
            bands={k: v for k, v in cfg.items() if k not in {"label", "unit"}},
        )
        for metric, cfg in (load_thresholds().get("metrics") or {}).items()
    ]


def _change_pct(now: Decimal | None, then: Decimal | None) -> float | None:
    if now is None or then is None or then == 0:
        return None
    return round(float((now - then) / abs(then) * 100), 1)


def metric_tiles(
    session: Session,
    issuer: Issuer,
    as_of: date,
    *,
    only: tuple[str, ...] = METRIC_IDS,
) -> list[MetricTileOut]:
    """One tile per metric: latest value, 1-year change, flag, full sparkline."""
    rows = session.scalars(
        select(CreditMetric)
        .where(CreditMetric.issuer_id == issuer.id, CreditMetric.filed <= as_of)
        .order_by(CreditMetric.period_end.asc())
    ).all()
    by_metric: dict[str, list[CreditMetric]] = {}
    for row in rows:
        by_metric.setdefault(row.metric, []).append(row)
    cfg = load_metric_config().for_issuer(issuer.key or "")
    tiles: list[MetricTileOut] = []
    for metric in only:
        series = by_metric.get(metric, [])
        missing_reason: str | None = None
        if not series and cfg is not None:
            if not cfg.usable:
                missing_reason = cfg.unusable_reason
            else:
                for field_name in _fields_for_metric(metric):
                    reason = cfg.reason(field_name)
                    if reason:
                        missing_reason = reason
                        break
                missing_reason = missing_reason or "no data yet"
        detail = dict(series[-1].detail or {}) if series else {}
        if not series:
            tiles.append(
                MetricTileOut(
                    metric=metric,
                    label=METRIC_LABELS[metric],
                    value=None,
                    unit="x" if metric in _RATIO_METRICS else "USD",
                    missing_reason=missing_reason or "no data yet",
                )
            )
            continue
        latest = series[-1]
        prior = None
        cutoff = latest.period_end - timedelta(days=365)
        for row in reversed(series[:-1]):
            if row.period_end <= cutoff:
                prior = row
                break
        tiles.append(
            MetricTileOut(
                metric=metric,
                label=METRIC_LABELS[metric],
                value=latest.value,
                unit=latest.unit,
                fiscal_label=latest.fiscal_label,
                period_end=latest.period_end,
                filed=latest.filed,
                source_tag=latest.source_tag,
                missing_reason=(detail.get("missing") if latest.value is None else None),
                change_pct=_change_pct(latest.value, prior.value if prior else None),
                flag=flag_for(metric, latest.value),
                cash_only=detail.get("cash_only"),
                wall_disagrees=detail.get("wall_agrees") is False,
                wall_value=Decimal(str(detail["wall_due"])) if detail.get("wall_due") else None,
                sparkline=[
                    SparkPoint(period_end=r.period_end, label=r.fiscal_label, value=r.value)
                    for r in series
                ],
            )
        )
    return tiles


_RATIO_METRICS = frozenset(
    {"leverage", "net_leverage", "interest_coverage", "liquidity_coverage", "fcf_to_debt"}
)


def _fields_for_metric(metric: str) -> tuple[str, ...]:
    return {
        "total_debt": ("debt_total", "debt_current"),
        "cash": ("cash",),
        "st_investments": ("st_investments",),
        "due_12m": ("mat_next12",),
        "due_24m": ("mat_next12", "mat_year2"),
        "liquidity_coverage": ("cash", "mat_next12"),
        "ebitda": ("opinc", "depreciation"),
        "leverage": ("opinc", "depreciation"),
        "net_leverage": ("opinc", "depreciation", "cash"),
        "interest_coverage": ("opinc", "depreciation", "interest"),
        "fcf": ("ocf", "capex"),
        "fcf_to_debt": ("ocf", "capex"),
    }.get(metric, ())


def _feed_before_after(session: Session, alert: Alert) -> BeforeAfterOut | None:
    for change in _applied_changes(session, alert.run_id):
        if change.before is None and change.after is None:
            continue
        return BeforeAfterOut(field=change.field, before=change.before, after=change.after)
    return None


def _plain_words_eval(row: ReplayEvaluation | None) -> EvalSummaryOut | None:
    if row is None:
        return None
    per_event = row.per_event or []
    judged = [e for e in per_event if isinstance(e, dict) and e.get("expected_alert") is not None]
    material = [e for e in judged if e.get("expected_alert") is True]
    caught = [e for e in material if not e.get("miss")]
    false_alerts = [
        e
        for e in judged
        if e.get("expected_alert") is False and str(e.get("got") or "").lower().startswith("alert")
    ]
    if judged:
        tail = (
            "no false alerts"
            if not false_alerts
            else f"{len(false_alerts)} false alert{'s' if len(false_alerts) != 1 else ''}"
        )
        text = (
            f"Tested on {len(judged)} real 2026 events: caught {len(caught)} of "
            f"{len(material)} material changes, {tail}"
        )
    elif row.event_count:
        text = f"Tested on {row.event_count} real 2026 events."
    else:
        text = "Evaluation pending."
    return EvalSummaryOut(
        text=text,
        evaluated_at=row.evaluated_at.isoformat() if row.evaluated_at else None,
        precision=float(row.precision) if row.precision is not None else None,
        recall=float(row.recall) if row.recall is not None else None,
        incomplete=bool(row.incomplete),
    )


@router.get("/overview", response_model=CreditOverviewOut)
def credit_overview(
    session: DbSession,
    limit: int = Query(20, ge=1, le=100),
) -> CreditOverviewOut:
    """What changed: header counts, impact feed, watchlist, evaluation."""
    limit = clamp_limit(limit)
    roots = session.scalars(
        select(Issuer).where(Issuer.parent_id.is_(None)).order_by(Issuer.key)
    ).all()
    filings_read = session.scalar(select(func.count()).select_from(Filing)) or 0
    material_changes = session.scalar(select(func.count()).select_from(Alert)) or 0
    last_check = session.scalar(
        select(func.max(Event.event_date)).where(Event.status == "processed")
    )
    header = OverviewHeaderOut(
        companies=len(roots),
        filings_read=int(filings_read),
        material_changes=int(material_changes),
        last_check=last_check,
    )
    feed: list[FeedItemOut] = []
    for alert in session.scalars(
        select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    ).all():
        issuer = session.get(Issuer, alert.issuer_id)
        if issuer is None:
            continue
        event = session.get(Event, alert.event_id) if alert.event_id else None
        sentence, pending, _ = impact_for_alert(session, alert)
        feed.append(
            FeedItemOut(
                alert_id=alert.id,
                issuer=_issuer_ref(issuer),
                event_date=event.event_date if event else None,
                title=alert.title,
                impact_sentence=sentence or alert.summary,
                impact_pending=pending,
                before_after=_feed_before_after(session, alert),
            )
        )
    watchlist = [
        WatchlistRowOut(
            issuer=_issuer_ref(iss),
            ticker=iss.ticker,
            metrics=metric_tiles(
                session,
                iss,
                date.today(),
                only=(
                    "total_debt",
                    "due_24m",
                    "liquidity_coverage",
                    "leverage",
                    "interest_coverage",
                ),
            ),
        )
        for iss in roots
        if iss.key
    ]
    latest_eval = session.scalars(
        select(ReplayEvaluation).order_by(ReplayEvaluation.evaluated_at.desc())
    ).first()
    return CreditOverviewOut(
        header=header,
        feed=feed,
        watchlist=watchlist,
        evaluation=_plain_words_eval(latest_eval),
        thresholds=threshold_legend(),
    )
