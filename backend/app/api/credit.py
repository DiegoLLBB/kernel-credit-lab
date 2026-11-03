"""Analyst-layer read API: "What changed" overview and shared tile builders.

Plain-English endpoints over credit_metric rows, maturity walls and checked
insight text. Every number comes from the database; missing data is reported
with its reason, never filled in.
"""

from __future__ import annotations

from datetime import date, datetime
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
    BriefRefusedOut,
    BriefSentenceOut,
    ChartCrosscheckSummaryOut,
    CreditOverviewOut,
    DailyBriefOut,
    EvalComparisonOut,
    EvalComparisonRowOut,
    EvalDetailEventOut,
    EvalSummaryOut,
    FeedItemOut,
    LiveDocumentOut,
    LiveSectionOut,
    MetricPeerOut,
    MetricTileOut,
    OverviewHeaderOut,
    SparkPoint,
    ThresholdBandOut,
    WatchlistRowOut,
)
from app.charts.crosscheck import crosscheck_verified_charts, summarize_crosschecks
from app.charts.facts_cache import load_companyfacts
from app.db import get_session
from app.eval.replay_verdict import replay_verdict_for_alert
from app.eval.rescore import build_comparison
from app.eval.summary import build_eval_summary
from app.ingest.live import live_stats
from app.insight.brief import latest_brief
from app.insight.display import format_multiple_display, format_usd_display
from app.metrics.compute import METRIC_IDS
from app.metrics.config import load_metric_config
from app.metrics.peers import (
    BANK_NA_METRICS,
    BANK_NA_REASON,
    is_bank_sector,
    peer_comparison,
    sector_label_for_display,
)
from app.metrics.periods import prior_same_period
from app.models import Alert, CreditMetric, Event, Filing, Issuer, LiveConfig
from app.models.trace import AgentRun

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
_RATIO_METRICS = frozenset(
    {"leverage", "net_leverage", "interest_coverage", "liquidity_coverage", "fcf_to_debt"}
)


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


def _format_peer_figure(value: Decimal | None, unit: str) -> str | None:
    if value is None:
        return None
    if unit == "USD":
        return format_usd_display(float(value))
    return format_multiple_display(float(value))


def _peer_out(
    session: Session,
    issuer: Issuer,
    metric: str,
    as_of: date,
    *,
    value: Decimal | None,
    unit: str,
) -> MetricPeerOut | None:
    raw = peer_comparison(session, issuer, metric, as_of, value=value)
    if raw is None:
        return None
    sector = raw.get("sector")
    n = int(raw.get("n") or 0)
    median = raw.get("median")
    rank = raw.get("rank")
    ordinal = raw.get("ordinal")
    note = raw.get("note")
    value_disp = _format_peer_figure(value, unit)
    if note and (rank is None or median is None):
        line = note
    elif value_disp and median is not None and ordinal and n:
        med_disp = _format_peer_figure(Decimal(str(median)), unit)
        sector_bit = sector_label_for_display(sector if isinstance(sector, str) else None)
        line = f"{value_disp} · {sector_bit} median {med_disp} · {ordinal} of {n}"
    else:
        line = note
    return MetricPeerOut(
        sector=sector if isinstance(sector, str) else None,
        n=n,
        median=Decimal(str(median)) if median is not None else None,
        rank=rank if isinstance(rank, int) else None,
        ordinal=ordinal if isinstance(ordinal, str) else None,
        note=note if isinstance(note, str) else None,
        line=line,
    )


def metric_tiles(
    session: Session,
    issuer: Issuer,
    as_of: date,
    *,
    only: tuple[str, ...] = METRIC_IDS,
    include_peers: bool = True,
) -> list[MetricTileOut]:
    """One tile per metric: latest value, 1-year change, flag, full sparkline.

    ``include_peers=False`` skips sector peer medians (overview watchlist) —
    peer scans dominate overview latency and the watchlist does not show them.
    """
    rows = session.scalars(
        select(CreditMetric)
        .where(CreditMetric.issuer_id == issuer.id, CreditMetric.filed <= as_of)
        .order_by(CreditMetric.period_end.asc())
    ).all()
    by_metric: dict[str, list[CreditMetric]] = {}
    for row in rows:
        by_metric.setdefault(row.metric, []).append(row)
    cfg = load_metric_config().for_issuer(issuer.key or "")
    bank = is_bank_sector(issuer.sector)
    tiles: list[MetricTileOut] = []
    for metric in only:
        if bank and metric in BANK_NA_METRICS:
            tiles.append(
                MetricTileOut(
                    metric=metric,
                    label=METRIC_LABELS[metric],
                    value=None,
                    unit="x" if metric in _RATIO_METRICS else "USD",
                    missing_reason=BANK_NA_REASON,
                )
            )
            continue
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
        latest = next((r for r in reversed(series) if r.value is not None), series[-1])
        detail = dict(latest.detail or {})
        prior = prior_same_period(series, latest)
        prior_val = prior.value if prior else None
        # Liquidity never includes undrawn credit lines (rarely tagged) and has
        # no risk band — a cash ratio alone is not a full liquidity judgement.
        flag = (
            None
            if metric == "liquidity_coverage"
            else flag_for(metric, latest.value, sector=issuer.sector)
        )
        source_note = detail.get("source_note")
        # Annual-only maturity / cash-cover tiles: label even if the stored row
        # predates source_note (seed refresh writes it into detail).
        if (
            not source_note
            and metric in {"due_12m", "due_24m", "liquidity_coverage"}
            and (latest.fiscal_label or "").startswith("FY")
            and latest.value is not None
        ):
            source_note = f"from the {latest.fiscal_label} 10-K"
        peer = (
            _peer_out(
                session,
                issuer,
                metric,
                as_of,
                value=latest.value,
                unit=latest.unit,
            )
            if include_peers and latest.value is not None and metric in _RATIO_METRICS
            else None
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
                source_note=source_note,
                peer=peer,
                sparkline=[
                    SparkPoint(period_end=r.period_end, label=r.fiscal_label, value=r.value)
                    for r in series
                ],
            )
        )
    return tiles


def _fields_for_metric(metric: str) -> tuple[str, ...]:
    return {
        "total_debt": ("debt_total", "debt_current"),
        "cash": ("cash",),
        "st_investments": ("st_investments",),
        "due_12m": ("mat_next12",),
        "due_24m": ("mat_next12", "mat_year2"),
        "liquidity_coverage": ("cash", "mat_next12"),
        "ebitda": ("opinc", "pretax", "depreciation", "interest"),
        "leverage": ("opinc", "pretax", "depreciation", "interest"),
        "net_leverage": ("opinc", "pretax", "depreciation", "interest", "cash"),
        "interest_coverage": ("opinc", "pretax", "depreciation", "interest"),
        "fcf": ("ocf", "capex"),
        "fcf_to_debt": ("ocf", "capex"),
    }.get(metric, ())


def _feed_before_after(session: Session, alert: Alert) -> BeforeAfterOut | None:
    from app.api.wall_delta import primary_wall_before_after

    primary = primary_wall_before_after(session, alert)
    if primary is not None:
        return BeforeAfterOut(
            field=primary.get("field") or primary.get("bucket"),
            before=primary.get("before"),
            after=primary.get("after"),
            before_display=primary.get("before_display"),
            after_display=primary.get("after_display"),
        )
    for change in _applied_changes(session, alert.run_id):
        if change.before is None and change.after is None:
            continue
        from app.insight.display import format_usd_display

        b, a = change.before, change.after
        return BeforeAfterOut(
            field=change.field,
            before=b,
            after=a,
            before_display=format_usd_display(b) if isinstance(b, (int, float)) else None,
            after_display=format_usd_display(a) if isinstance(a, (int, float)) else None,
        )
    return None


def _eval_summary_out(session: Session, *, kind: str = "agent") -> EvalSummaryOut | None:
    data = build_eval_summary(session, kind=kind)
    if data is None:
        return None
    return EvalSummaryOut(
        text=data["text"],
        label=data.get("label"),
        kind=data.get("kind") or kind,
        evaluated_at=data["evaluated_at"],
        precision=data["precision"],
        recall=data["recall"],
        incomplete=data["incomplete"],
        caught=data["caught"],
        expected_material=data["expected_material"],
        false_alert_count=data["false_alerts"],
        unsupported_claims=data.get("unsupported_claims"),
        accurate_caught=data.get("accurate_caught"),
        accurate_total=data.get("accurate_total"),
        keeps_records=data.get("keeps_records"),
        wall_passed=data.get("wall_passed"),
        wall_total=data.get("wall_total"),
        cost_usd=data.get("cost_usd"),
        missed_events=[EvalDetailEventOut(**e) for e in data["missed_events"]],
        false_events=[EvalDetailEventOut(**e) for e in data["false_events"]],
    )


def _chart_crosscheck_summary(session: Session) -> ChartCrosscheckSummaryOut | None:
    """Aggregate verified-chart vs filing checks for the overview line."""
    try:
        issuers = session.scalars(select(Issuer).where(Issuer.cik.is_not(None))).all()
        facts_by_cik: dict[str, Any] = {}
        for iss in issuers:
            if not iss.cik:
                continue
            doc = load_companyfacts(iss.cik)
            if doc is not None:
                facts_by_cik[iss.cik] = doc
                facts_by_cik[iss.cik.lstrip("0") or "0"] = doc
        results = crosscheck_verified_charts(session, companyfacts_by_cik=facts_by_cik)
        counts = summarize_crosschecks(results)
        line = (
            f"Chart values checked against filings: {counts['agree']} agree, "
            f"{counts['differ']} differ, {counts['not_comparable']} not comparable"
        )
        return ChartCrosscheckSummaryOut(
            agree=counts["agree"],
            differ=counts["differ"],
            not_comparable=counts["not_comparable"],
            line=line,
        )
    except Exception:
        return None


def _comparison_out(session: Session) -> EvalComparisonOut | None:
    try:
        data = build_comparison(session)
    except Exception:
        return None
    if not data.get("rows"):
        return None
    return EvalComparisonOut(
        rows=[EvalComparisonRowOut(**r) for r in data["rows"]],
        # Walkthrough: table + footnote + chart line only — no prose that repeats the table.
        note=None,
        chart_crosscheck=_chart_crosscheck_summary(session),
    )


def _brief_out(session: Session) -> DailyBriefOut | None:
    row = latest_brief(session)
    if row is None:
        return None
    if row.status in {"empty", "error"} and not (row.sentences or []):
        return None
    links = row.links if isinstance(row.links, list) else []
    check = row.check if isinstance(row.check, dict) else {}
    writer_by_sentence = {
        str(item.get("sentence")): str(item.get("source"))
        for item in (check.get("sentence_sources") or [])
        if isinstance(item, dict) and item.get("sentence") and item.get("source")
    }
    sentences: list[BriefSentenceOut] = []
    if links:
        for item in links:
            if isinstance(item, dict) and item.get("sentence"):
                text = str(item["sentence"])
                sentences.append(
                    BriefSentenceOut(
                        sentence=text,
                        refs=list(item.get("refs") or []),
                        writer=writer_by_sentence.get(text),
                    )
                )
    else:
        for s in row.sentences or []:
            text = str(s)
            sentences.append(
                BriefSentenceOut(
                    sentence=text,
                    refs=[],
                    writer=writer_by_sentence.get(text),
                )
            )
    verifier = check.get("verifier_models") or []
    check_model = verifier[0] if verifier else "Muse Spark Open"
    refused_rows: list[BriefRefusedOut] = []
    for item in row.dropped or []:
        if not isinstance(item, dict):
            continue
        # Attribution-only notes are not "refused" claims.
        reason_raw = str(item.get("reason") or "")
        if reason_raw in {
            "superseded by template pack",
            "template unused (Wonder passed ≥3)",
            "template duplicate of Wonder sentence",
        }:
            continue
        sentence = item.get("sentence")
        reason = item.get("reason") or item.get("problem")
        if sentence is None and reason is None:
            continue
        refused_rows.append(
            BriefRefusedOut(
                sentence=str(sentence) if sentence else None,
                reason=str(reason) if reason else None,
            )
        )
    refused = len(refused_rows)
    from app.llm.pricing import (
        COST_UNKNOWN,
        TOTAL_EXCL_UNPRICED,
        cost_label_for_call,
        is_unpriced_model,
    )

    raw_cost = float(row.cost_usd) if row.cost_usd is not None else None
    # Stored 0.0 from before unpriced handling still means unknown for Wonder.
    if is_unpriced_model(row.model) and (raw_cost is None or raw_cost == 0.0):
        cost_usd = None
        cost_label = COST_UNKNOWN
    elif raw_cost is not None and check.get("unpriced_calls"):
        cost_usd = raw_cost
        cost_label = (
            f"${raw_cost:.4f} ({TOTAL_EXCL_UNPRICED})"
            if raw_cost < 1
            else f"${raw_cost:.2f} ({TOTAL_EXCL_UNPRICED})"
        )
    elif raw_cost is not None:
        cost_usd = raw_cost
        cost_label = cost_label_for_call(row.model, raw_cost)
    else:
        cost_usd = None
        cost_label = COST_UNKNOWN if is_unpriced_model(row.model) else None
    cost_bit = f" · {cost_label}" if cost_label else ""
    wonder_n = int(check.get("wonder_count") or 0)
    template_n = int(check.get("template_count") or 0)
    if wonder_n or template_n:
        bits: list[str] = []
        if wonder_n and template_n:
            tmpl = "a template" if template_n == 1 else "templates"
            bits.append(
                f"{wonder_n} sentence{'s' if wonder_n != 1 else ''} by Muse Wonder, "
                f"{template_n} from {tmpl}"
            )
        elif wonder_n:
            bits.append(f"{wonder_n} sentence{'s' if wonder_n != 1 else ''} by Muse Wonder")
        else:
            tmpl = "a template" if template_n == 1 else "templates"
            bits.append(f"{template_n} sentence{'s' if template_n != 1 else ''} from {tmpl}")
        bits.append("all checked by Muse Spark Open")
        if refused:
            bits.append(f"{refused} sentence{'s' if refused != 1 else ''} refused")
        attribution = " · ".join(bits) + cost_bit
    else:
        attribution = (
            f"Written by Muse Wonder · checked by Muse Spark Open · "
            f"{refused} sentence{'s' if refused != 1 else ''} refused{cost_bit}"
        )
    return DailyBriefOut(
        as_of=row.as_of,
        status=row.status,
        sentences=sentences,
        refused=refused_rows,
        refused_count=refused,
        provider=row.provider,
        model=row.model,
        check_model=str(check_model),
        cost_usd=cost_usd,
        cost_label=cost_label,
        model_calls=int(row.model_calls or 0),
        attribution=attribution,
    )


def _feed_item(session: Session, alert: Alert, *, live: bool) -> FeedItemOut | None:
    issuer = session.get(Issuer, alert.issuer_id)
    if issuer is None:
        return None
    event = session.get(Event, alert.event_id) if alert.event_id else None
    kind = getattr(alert, "kind", None) or "agent"
    filing_url = None
    if kind == "metric":
        after = alert.after_metrics if isinstance(alert.after_metrics, dict) else {}
        filing_url = after.get("filing_url")
        sentence, pending = alert.summary, False
        verdict, verdict_reason = None, None
        ba = None
        if after.get("metric") and alert.before_metrics:
            before = alert.before_metrics if isinstance(alert.before_metrics, dict) else {}
            from app.insight.display import format_multiple_display, format_usd_display

            b, a = before.get("value"), after.get("value")
            before_disp = before.get("display")
            after_disp = after.get("display")
            if not before_disp and b is not None:
                before_disp = (
                    format_usd_display(b)
                    if after.get("metric") == "due_12m"
                    else format_multiple_display(float(b))
                )
            if not after_disp and a is not None:
                after_disp = (
                    format_usd_display(a)
                    if after.get("metric") == "due_12m"
                    else format_multiple_display(float(a))
                )
            ba = BeforeAfterOut(
                field=str(after.get("period") or after.get("metric")),
                before=b,
                after=a,
                before_display=before_disp,
                after_display=after_disp,
            )
    else:
        sentence, pending, _ = impact_for_alert(session, alert)
        verdict, verdict_reason = replay_verdict_for_alert(session, alert)
        ba = _feed_before_after(session, alert)
    return FeedItemOut(
        alert_id=alert.id,
        issuer=_issuer_ref(issuer),
        event_date=(
            event.event_date if event else (alert.created_at.date() if alert.created_at else None)
        ),
        title=alert.title,
        impact_sentence=sentence or alert.summary,
        impact_pending=pending,
        before_after=ba,
        live=live,
        replay_verdict=verdict,
        replay_verdict_reason=verdict_reason,
        created_at=alert.created_at,
        kind=kind if kind in {"agent", "metric"} else "agent",
        filing_url=filing_url,
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
            .where(
                Alert.kind == "agent",
                Alert.event_id.is_(None) | ~Alert.event_id.in_(live_event_ids),
            )
        )
        or 0
    )
    replay_last = session.scalar(
        select(func.max(Event.event_date)).where(
            Event.status == "processed", Event.source != "live"
        )
    )
    live_cfg = session.scalars(select(LiveConfig).limit(1)).first()
    live_detected = session.scalar(
        select(func.max(Event.detected_at)).where(Event.source == "live")
    )
    live_run = session.scalar(
        select(func.max(AgentRun.completed_at))
        .join(Event, Event.run_id == AgentRun.id)
        .where(Event.source == "live")
    )
    candidates: list[datetime] = []
    if live_cfg is not None:
        candidates.append(live_cfg.go_live_at)
    if live_detected is not None:
        candidates.append(live_detected)
    if live_run is not None:
        candidates.append(live_run)
    last_check = max(candidates).date() if candidates else replay_last
    header = OverviewHeaderOut(
        companies=len(roots),
        filings_read=int(filings_read),
        material_changes=int(material_changes),
        last_check=last_check,
    )
    # Cap the scan: building feed items (esp. agent impact) over every historical
    # alert was the main overview latency source (~5s). We only surface
    # ``limit`` agent + ``max(limit, 40)`` metric rows plus a live slice.
    metric_cap = max(limit, 40)
    scan_cap = max(limit, metric_cap) * 4
    recent_alerts = session.scalars(
        select(Alert).order_by(Alert.created_at.desc()).limit(scan_cap)
    ).all()
    feed_items: list[FeedItemOut] = []
    for alert in recent_alerts:
        event = session.get(Event, alert.event_id) if alert.event_id else None
        live = event is not None and event.source == "live"
        item = _feed_item(session, alert, live=live)
        if item is not None:
            feed_items.append(item)
    feed_items.sort(
        key=lambda i: (
            i.event_date or date.min,
            i.created_at or datetime.min,
        ),
        reverse=True,
    )
    # Agent alerts lead the feed; metric alerts follow (UI collapses them).
    agent_feed = [i for i in feed_items if i.kind != "metric"]
    metric_feed = [i for i in feed_items if i.kind == "metric"]
    feed = agent_feed[:limit] + metric_feed[:metric_cap]
    live_feed = [i for i in feed_items if i.live][:limit]
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
            sector=iss.sector,
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
                include_peers=False,
            ),
        )
        for iss in roots
        if iss.key
    ]
    return CreditOverviewOut(
        header=header,
        brief=_brief_out(session),
        feed=feed,
        watchlist=watchlist,
        evaluation=_eval_summary_out(session, kind="agent"),
        baseline=_eval_summary_out(session, kind="baseline"),
        comparison=_comparison_out(session),
        live=live_section,
        thresholds=threshold_legend(),
    )
