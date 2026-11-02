"""Analyst-layer read API: "What changed" overview and shared tile builders.

Plain-English endpoints over credit_metric rows, maturity walls and checked
insight text. Every number comes from the database; missing data is reported
with its reason, never filled in.
"""

from __future__ import annotations

from datetime import date
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
    EvalDetailEventOut,
    EvalSummaryOut,
    FeedItemOut,
    LiveDocumentOut,
    LiveSectionOut,
    MetricTileOut,
    OverviewHeaderOut,
    SparkPoint,
    ThresholdBandOut,
    WatchlistRowOut,
)
from app.db import get_session
from app.eval.summary import build_eval_summary
from app.ingest.live import live_stats
from app.metrics.compute import METRIC_IDS
from app.metrics.config import load_metric_config
from app.metrics.periods import prior_same_period
from app.models import Alert, CreditMetric, Event, Filing, Issuer

router = APIRouter(prefix="/api/credit", tags=["credit"])
DbSession = Annotated[Session, Depends(get_session)]

METRIC_LABELS = {
    "total_debt": "Total debt",
    "cash": "Cash",
    "st_investments": "Short-term investments",
    "due_12m": "Debt due in 12 months",
    "due_24m": "Debt due in 24 months",
    "liquidity_coverage": "Cash only (excludes credit lines)",
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


_NEAR_ZERO = Decimal("0.05")


def _bands_for(metric: str, sector: str | None) -> dict[str, Any] | None:
    """Metric bands, with optional sector override for leverage-style ceilings."""
    cfg = (load_thresholds().get("metrics") or {}).get(metric)
    if not cfg:
        return None
    by_sector = cfg.get("by_sector") or {}
    if sector and sector in by_sector:
        merged = {k: v for k, v in cfg.items() if k not in {"by_sector", "label", "unit"}}
        merged.update(by_sector[sector])
        return merged
    return {k: v for k, v in cfg.items() if k not in {"by_sector", "label", "unit"}}


def flag_for(metric: str, value: Decimal | None, *, sector: str | None = None) -> str | None:
    """Green/amber/red screening flag, None where no band applies."""
    if value is None:
        return None
    bands = _bands_for(metric, sector)
    if not bands:
        return None
    if "green_max" in bands:
        if value <= Decimal(str(bands["green_max"])):
            return "green"
        return "amber" if value <= Decimal(str(bands["amber_max"])) else "red"
    if "green_min" not in bands:
        return None
    if value >= Decimal(str(bands["green_min"])):
        return "green"
    return "amber" if value >= Decimal(str(bands["amber_min"])) else "red"


def threshold_legend() -> list[ThresholdBandOut]:
    out: list[ThresholdBandOut] = []
    for metric, cfg in (load_thresholds().get("metrics") or {}).items():
        bands = {k: v for k, v in cfg.items() if k not in {"label", "unit", "by_sector"}}
        out.append(
            ThresholdBandOut(
                label=cfg.get("label", metric),
                unit=cfg.get("unit", "x"),
                bands=bands,
            )
        )
        for sector, override in (cfg.get("by_sector") or {}).items():
            out.append(
                ThresholdBandOut(
                    label=f"{cfg.get('label', metric)} · {sector}",
                    unit=cfg.get("unit", "x"),
                    bands=dict(override),
                )
            )
    return out


def _change_pct(now: Decimal | None, then: Decimal | None) -> float | None:
    """Year-on-year % change; None when prior is missing, ≤0, or near zero."""
    if now is None or then is None or then <= 0 or abs(then) <= _NEAR_ZERO:
        return None
    return round(float((now - then) / abs(then) * 100), 1)


def _prior_for_display(then: Decimal | None) -> Decimal | None:
    """Prior value for the UI; suppressed when ≤0 or near zero."""
    if then is None or then <= 0 or abs(then) <= _NEAR_ZERO:
        return None
    return then


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
        prior = prior_same_period(series, latest)
        prior_val = prior.value if prior else None
        # Liquidity never includes undrawn credit lines (rarely tagged) and has
        # no risk band — a cash ratio alone is not a full liquidity judgement.
        flag = (
            None
            if metric == "liquidity_coverage"
            else flag_for(metric, latest.value, sector=issuer.sector)
        )
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
                change_pct=_change_pct(latest.value, prior_val),
                prior_value=_prior_for_display(prior_val),
                prior_period_end=prior.period_end if prior else None,
                flag=flag,
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


def _eval_summary_out(session: Session) -> EvalSummaryOut | None:
    data = build_eval_summary(session)
    if data is None:
        return None
    return EvalSummaryOut(
        text=data["text"],
        evaluated_at=data["evaluated_at"],
        precision=data["precision"],
        recall=data["recall"],
        incomplete=data["incomplete"],
        caught=data["caught"],
        expected_material=data["expected_material"],
        false_alert_count=data["false_alerts"],
        missed_events=[EvalDetailEventOut(**e) for e in data["missed_events"]],
        false_events=[EvalDetailEventOut(**e) for e in data["false_events"]],
    )


def _feed_item(session: Session, alert: Alert, *, live: bool) -> FeedItemOut | None:
    issuer = session.get(Issuer, alert.issuer_id)
    if issuer is None:
        return None
    event = session.get(Event, alert.event_id) if alert.event_id else None
    sentence, pending, _ = impact_for_alert(session, alert)
    return FeedItemOut(
        alert_id=alert.id,
        issuer=_issuer_ref(issuer),
        event_date=event.event_date if event else None,
        title=alert.title,
        impact_sentence=sentence or alert.summary,
        impact_pending=pending,
        before_after=_feed_before_after(session, alert),
        live=live,
    )


@router.get("/overview", response_model=CreditOverviewOut)
def credit_overview(
    session: DbSession,
    limit: int = Query(20, ge=1, le=100),
) -> CreditOverviewOut:
    """What changed: replay feed + live section + watchlist + evaluation."""
    limit = clamp_limit(limit)
    roots = session.scalars(
        select(Issuer).where(Issuer.parent_id.is_(None)).order_by(Issuer.key)
    ).all()
    # Replay-only material changes: live alerts never mix into the score header.
    # Alerts with no event_id (legacy/test) still count as replay.
    filings_read = session.scalar(select(func.count()).select_from(Filing)) or 0
    live_event_ids = select(Event.id).where(Event.source == "live")
    material_changes = (
        session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.event_id.is_(None) | ~Alert.event_id.in_(live_event_ids))
        )
        or 0
    )
    last_check = session.scalar(
        select(func.max(Event.event_date)).where(
            Event.status == "processed", Event.source != "live"
        )
    )
    header = OverviewHeaderOut(
        companies=len(roots),
        filings_read=int(filings_read),
        material_changes=int(material_changes),
        last_check=last_check,
    )
    feed: list[FeedItemOut] = []
    for alert in session.scalars(select(Alert).order_by(Alert.created_at.desc())).all():
        event = session.get(Event, alert.event_id) if alert.event_id else None
        if event is not None and event.source == "live":
            continue
        item = _feed_item(session, alert, live=False)
        if item is not None:
            feed.append(item)
        if len(feed) >= limit:
            break

    live_feed: list[FeedItemOut] = []
    for alert in session.scalars(select(Alert).order_by(Alert.created_at.desc())).all():
        event = session.get(Event, alert.event_id) if alert.event_id else None
        if event is None or event.source != "live":
            continue
        item = _feed_item(session, alert, live=True)
        if item is not None:
            live_feed.append(item)
        if len(live_feed) >= limit:
            break
    stats = live_stats(session)
    live_section = None
    if stats.get("go_live_at"):
        live_section = LiveSectionOut(
            since=stats["go_live_at"],
            git_sha=stats.get("git_sha"),
            prompt_version=stats.get("prompt_version"),
            documents_read=int(stats.get("documents_read") or 0),
            alerted=int(stats.get("alerted") or 0),
            logged=int(stats.get("logged") or 0),
            refused=int(stats.get("refused") or 0),
            paused=int(stats.get("paused") or 0),
            failed=int(stats.get("failed") or 0),
            documents=[LiveDocumentOut(**d) for d in (stats.get("documents") or [])],
            feed=live_feed,
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
    return CreditOverviewOut(
        header=header,
        feed=feed,
        watchlist=watchlist,
        evaluation=_eval_summary_out(session),
        live=live_section,
        thresholds=threshold_legend(),
    )
