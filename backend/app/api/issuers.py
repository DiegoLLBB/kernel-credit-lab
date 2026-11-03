"""Issuer list and detail with instruments, walls and event timeline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import HIDDEN_RUN_STATUSES, bucket_deltas, clamp_limit, json_safe
from app.api.credit import impact_for_alert, metric_tiles, threshold_legend
from app.api.schemas import (
    InsightHowOut,
    InstrumentOut,
    IssuerDetailOut,
    IssuerEventOut,
    IssuerListItem,
    IssuerListOut,
    IssuerRef,
    IssuerSnapshotOut,
    OnboardIssuerIn,
    OnboardIssuerOut,
    OnboardMissingOut,
    RefinancingBucketOut,
    RefinancingPanelOut,
    SnapshotInsightOut,
    SnapshotWallOut,
    TimelineItemOut,
    WallBucketOut,
    WallChangeOut,
)
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Alert, Event, Instrument, Issuer
from app.models.credit import CreditInsight
from app.models.trace import AgentRun
from app.onboard import OnboardError, onboard_ticker
from app.tools.maturity import instrument_baseline, instrument_state_at, maturity_wall
from app.tools.refinancing import (
    DEFAULT_TENOR_YEARS,
    FOOTNOTE,
    SHOCK_BPS_MAX,
    SHOCK_BPS_MIN,
    TENOR_YEARS,
    refinancing_panel,
)

router = APIRouter(prefix="/api/issuers", tags=["issuers"])
DbSession = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _ref(issuer: Issuer) -> IssuerRef:
    return IssuerRef(id=issuer.id, key=issuer.key, name=issuer.name)


def _list_item(session: Session, iss: Issuer) -> IssuerListItem:
    parent_key = None
    if iss.parent_id:
        parent = session.get(Issuer, iss.parent_id)
        parent_key = parent.key if parent else None
    sub_count = (
        session.scalar(select(func.count()).select_from(Issuer).where(Issuer.parent_id == iss.id))
        or 0
    )
    return IssuerListItem(
        id=iss.id,
        key=iss.key,
        name=iss.name,
        ticker=iss.ticker,
        sector=iss.sector,
        parent_key=parent_key,
        subsidiary_count=int(sub_count),
        onboard_source=iss.onboard_source,
        onboarded_at=iss.onboarded_at,
    )


def _require_admin_token(
    settings: Settings,
    x_admin_token: str | None,
    authorization: str | None,
) -> None:
    expected = (settings.admin_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin onboard is not configured (ADMIN_API_TOKEN missing)",
        )
    provided = (x_admin_token or "").strip()
    if not provided and authorization:
        auth = authorization.strip()
        provided = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="Admin token required")


@router.get("", response_model=IssuerListOut)
def list_issuers(
    session: DbSession,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> IssuerListOut:
    limit = clamp_limit(limit, default=100)
    total = session.scalar(select(func.count()).select_from(Issuer)) or 0
    rows = session.scalars(
        select(Issuer).order_by(Issuer.key.asc(), Issuer.name.asc()).offset(offset).limit(limit)
    ).all()
    items = [_list_item(session, iss) for iss in rows]
    onboarded = session.scalars(
        select(Issuer)
        .where(Issuer.onboard_source == "ticker")
        .order_by(Issuer.onboarded_at.desc().nullslast(), Issuer.key.asc())
    ).all()
    return IssuerListOut(
        items=items,
        total=int(total),
        limit=limit,
        offset=offset,
        onboarded_by_ticker=[_list_item(session, iss) for iss in onboarded],
    )


@router.post("", response_model=OnboardIssuerOut)
async def create_issuer_by_ticker(
    body: OnboardIssuerIn,
    session: DbSession,
    settings: SettingsDep,
    x_admin_token: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> OnboardIssuerOut:
    """Add a company by ticker (operator only). Loads XBRL metrics + maturity wall."""
    _require_admin_token(settings, x_admin_token, authorization)
    try:
        result = await onboard_ticker(session, body.ticker)
        session.commit()
    except OnboardError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Onboard failed: {exc}") from exc
    return OnboardIssuerOut(
        ticker=result.ticker,
        issuer_key=result.issuer_key,
        cik=result.cik,
        name=result.name,
        sector=result.sector,
        created=result.created,
        metrics_created=result.metrics_created,
        metrics_updated=result.metrics_updated,
        missing=[OnboardMissingOut(**m) for m in result.missing],
        wall_buckets=result.wall_buckets,
        wall_as_of=result.wall_as_of,
        snapshot_status=result.snapshot_status,
        monitor_from=result.monitor_from,
        cost_usd=result.cost_usd,
        elapsed_seconds=result.elapsed_seconds,
        message=result.message,
    )


@router.get("/{key}", response_model=IssuerDetailOut)
def get_issuer(
    key: str,
    session: DbSession,
    as_of: date | None = None,
    include_superseded: bool = False,
) -> IssuerDetailOut:
    issuer = session.scalars(select(Issuer).where(Issuer.key == key)).first()
    if issuer is None:
        raise HTTPException(status_code=404, detail="Issuer not found")

    point = as_of or date.today()
    parent = session.get(Issuer, issuer.parent_id) if issuer.parent_id else None
    subsidiaries = session.scalars(
        select(Issuer).where(Issuer.parent_id == issuer.id).order_by(Issuer.key)
    ).all()

    instruments_out: list[InstrumentOut] = []
    for inst in session.scalars(
        select(Instrument).where(Instrument.issuer_id == issuer.id).order_by(Instrument.maturity)
    ).all():
        amount, status = instrument_state_at(session, inst, point)
        original, _ = instrument_baseline(session, inst)
        # Skip instruments not yet issued as of point-in-time.
        if inst.issue_date and inst.issue_date > point:
            continue
        instruments_out.append(
            InstrumentOut(
                id=inst.id,
                key=inst.key,
                name=inst.name,
                instrument_type=inst.instrument_type,
                coupon=inst.coupon,
                rate_type=inst.rate_type,
                maturity=inst.maturity,
                maturity_precision=inst.maturity_precision,
                amount_outstanding=Decimal(amount),
                original_amount=Decimal(original),
                currency=inst.currency,
                status=status,
                issuer_id=inst.issuer_id,
                cusip=inst.cusip,
            )
        )

    issuer_wall = json_safe(maturity_wall(session, issuer.id, point))
    group_id = parent.id if parent is not None else issuer.id
    group_wall = json_safe(maturity_wall(session, group_id, point))
    group_wall_out = None if group_id == issuer.id and not subsidiaries else group_wall

    events_q = (
        select(Event)
        .where(Event.issuer_id == issuer.id)
        .order_by(Event.event_date.desc(), Event.created_at.desc())
    )
    events = session.scalars(events_q).all()
    timeline: list[IssuerEventOut] = []
    for ev in events:
        run_status = None
        run_id = ev.run_id
        if run_id:
            run = session.get(AgentRun, run_id)
            if run is not None:
                if (not include_superseded) and (run.status or "") in HIDDEN_RUN_STATUSES:
                    run_status = run.status
                    # Still show the event; surface run_status but keep id.
                else:
                    run_status = run.status
        alert_ids: list[str] = []
        story: str | None = None
        for a in session.scalars(select(Alert).where(Alert.event_id == ev.id)).all():
            if include_superseded or not a.run_id:
                alert_ids.append(a.id)
                if story is None and a.title:
                    story = a.title
                continue
            r = session.get(AgentRun, a.run_id)
            if r is None or (r.status or "") not in HIDDEN_RUN_STATUSES:
                alert_ids.append(a.id)
                if story is None and a.title:
                    story = a.title
        if story is None and run_id:
            run = session.get(AgentRun, run_id)
            if run is not None:
                result = run.result if isinstance(run.result, dict) else {}
                story = (
                    (result.get("summary") if isinstance(result, dict) else None)
                    or (result.get("log_summary") if isinstance(result, dict) else None)
                    or run.question
                )
                if isinstance(story, str):
                    story = story.strip() or None
        # Prefer the credit story over boilerplate "press release, 11 May 2026".
        summary_out = story or ev.summary
        timeline.append(
            IssuerEventOut(
                id=ev.id,
                replay_key=ev.replay_key,
                event_type=ev.event_type,
                event_date=ev.event_date,
                summary=summary_out,
                status=ev.status,
                run_id=run_id,
                run_status=run_status,
                alert_ids=alert_ids,
            )
        )

    return IssuerDetailOut(
        id=issuer.id,
        key=issuer.key,
        name=issuer.name,
        ticker=issuer.ticker,
        sector=issuer.sector,
        cik=issuer.cik,
        parent=_ref(parent) if parent else None,
        subsidiaries=[_ref(s) for s in subsidiaries],
        as_of=point,
        instruments=instruments_out,
        maturity_wall=issuer_wall,
        group_maturity_wall=group_wall_out,
        events=timeline,
    )


@router.get("/{key}/refinancing", response_model=RefinancingPanelOut)
def get_refinancing(
    key: str,
    session: DbSession,
    as_of: date | None = None,
    shock_bps: int = Query(0, ge=SHOCK_BPS_MIN, le=SHOCK_BPS_MAX),
    tenor_years: int = Query(DEFAULT_TENOR_YEARS),
) -> RefinancingPanelOut:
    """Display-only rate-reset cost and Treasury shock (0–300 bps). Not agent/alert input."""
    if tenor_years not in TENOR_YEARS:
        raise HTTPException(
            status_code=422,
            detail=f"tenor_years must be one of {list(TENOR_YEARS)}",
        )
    issuer = session.scalars(select(Issuer).where(Issuer.key == key)).first()
    if issuer is None:
        raise HTTPException(status_code=404, detail="Issuer not found")
    point = as_of or date.today()
    panel = refinancing_panel(
        session, issuer.id, point, shock_bps=shock_bps, tenor_years=tenor_years
    )
    base = panel["base"]
    shocked = panel["shocked"]
    spread = base.get("spread") or {}
    bench = base.get("benchmark") or spread.get("benchmark") or {}
    cod = spread.get("cost_of_debt") or {}

    def _opt(raw: Any) -> Decimal | None:
        if raw is None or raw == "":
            return None
        return Decimal(str(raw))

    buckets = [
        RefinancingBucketOut(
            label=str(b["label"]),
            total_outstanding=_opt(b.get("total_outstanding")),
            named_amount=_opt(b.get("named_amount")),
            unattributed_amount=_opt(b.get("unattributed_amount")),
            maturing_coupon_pct=_opt(b.get("maturing_coupon_pct")),
            maturing_coupon_source=b.get("maturing_coupon_source"),
            remainder_coupon_pct=_opt(b.get("remainder_coupon_pct")),
            remainder_coupon_source=b.get("remainder_coupon_source"),
            incremental_annual_interest=_opt(b.get("incremental_annual_interest")),
        )
        for b in (base.get("buckets") or [])
    ]

    insight_row = session.scalars(
        select(CreditInsight)
        .where(
            CreditInsight.issuer_id == issuer.id,
            CreditInsight.scope == "refinancing",
            CreditInsight.ref == "0bp",
            CreditInsight.as_of <= point,
        )
        .order_by(CreditInsight.as_of.desc())
    ).first()
    insight_sentence = None
    insight_status = None
    if insight_row is not None and insight_row.sentences:
        insight_sentence = str(insight_row.sentences[0])
        insight_status = insight_row.status

    tsy_obs = None
    if bench.get("obs_date"):
        tsy_obs = date.fromisoformat(str(bench["obs_date"])[:10])

    return RefinancingPanelOut(
        as_of=point,
        tenor_years=int(panel["tenor_years"]),
        tenor_choices=list(panel.get("tenor_choices") or TENOR_YEARS),
        shock_bps=int(panel["shock_bps"]),
        shock_bps_min=int(panel["shock_bps_min"]),
        shock_bps_max=int(panel["shock_bps_max"]),
        derivation_line=base.get("derivation_line") or spread.get("derivation_line"),
        base_scenario_line=panel.get("base_scenario_line"),
        spread_pct=_opt(spread.get("spread_pct")),
        spread_bps=spread.get("spread_bps"),
        treasury_pct=_opt(bench.get("value")),
        treasury_obs_date=tsy_obs,
        cost_of_debt_pct=_opt(cod.get("rate_pct")),
        cost_of_debt_label=cod.get("fiscal_label"),
        base_total=_opt(base.get("total_incremental_annual_interest")),
        named_base=_opt(base.get("named_incremental_annual_interest")),
        estimated_base=_opt(base.get("estimated_incremental_annual_interest")),
        shocked_total=_opt(shocked.get("total_incremental_annual_interest")),
        delta=_opt(panel.get("delta_incremental_annual_interest")),
        coverage_before=_opt(panel["coverage_base"].get("coverage_before")),
        coverage_after_base=_opt(panel["coverage_base"].get("coverage_after")),
        coverage_after_shock=_opt(panel["coverage_shocked"].get("coverage_after")),
        coverage_missing=panel["coverage_base"].get("missing"),
        missing=base.get("missing"),
        buckets=buckets,
        pressure_rank=panel.get("pressure_rank"),
        pressure_score=_opt(panel.get("pressure_score")),
        weights=dict(panel.get("weights") or {}),
        footnote=str(panel.get("footnote") or FOOTNOTE),
        insight_sentence=insight_sentence,
        insight_status=insight_status,
    )


@router.get("/{key}/snapshot", response_model=IssuerSnapshotOut)
def get_snapshot(
    key: str,
    session: DbSession,
    as_of: date | None = None,
) -> IssuerSnapshotOut:
    """Credit snapshot: checked insight text, metric tiles, wall, timeline."""
    issuer = session.scalars(select(Issuer).where(Issuer.key == key)).first()
    if issuer is None:
        raise HTTPException(status_code=404, detail="Issuer not found")
    point = as_of or date.today()

    insight_row = session.scalars(
        select(CreditInsight).where(
            CreditInsight.issuer_id == issuer.id,
            CreditInsight.scope == "snapshot",
            CreditInsight.as_of == point,
        )
    ).first()
    insight = None
    if insight_row is not None:
        # Refuse to serve prose whose grounded facts disagree with current tiles
        # (seed/live can refresh metrics after a snapshot was written). Skip the
        # gate when facts were never stored (legacy/test rows).
        from app.insight.generate import assert_snapshot_matches_metric_tiles

        stored = insight_row.facts if isinstance(insight_row.facts, dict) else {}
        if stored.get("metrics"):
            try:
                assert_snapshot_matches_metric_tiles(session, key, point, stored)
            except ValueError:
                insight_row = None
    if insight_row is not None:
        check = insight_row.check if isinstance(insight_row.check, dict) else {}
        insight = SnapshotInsightOut(
            headline=insight_row.headline,
            sentences=list(insight_row.sentences or []),
            status=insight_row.status,
            how=InsightHowOut(
                provider=insight_row.provider,
                model=insight_row.model,
                check_prompt=check.get("check_prompt"),
                dropped=list(insight_row.dropped or []),
            ),
        )

    wall_now = maturity_wall(session, issuer.id, point)
    year_start = date(point.year, 1, 1)
    wall_then = maturity_wall(session, issuer.id, year_start)
    buckets: list[WallBucketOut] = []
    for label, data in (wall_now.get("buckets") or {}).items():
        total = data.get("total_outstanding")
        buckets.append(
            WallBucketOut(
                label=label,
                amount=Decimal(str(total)) if total is not None else None,
            )
        )
    changes: list[WallChangeOut] = []
    window_alerts = session.scalars(
        select(Alert)
        .join(Event, Alert.event_id == Event.id)
        .where(
            Alert.issuer_id == issuer.id,
            Event.event_date > year_start,
            Event.event_date <= point,
        )
        .order_by(Event.event_date.desc())
    ).all()
    for delta in bucket_deltas(wall_then, wall_now):
        before = Decimal(delta["before"]) if delta.get("before") else None
        after = Decimal(delta["after"]) if delta.get("after") else None
        link = window_alerts[0] if window_alerts else None
        for alert in window_alerts:
            if (alert.title or "") and delta["label"] in (alert.title or ""):
                link = alert
                break
        for bucket in buckets:
            if bucket.label == delta["label"]:
                bucket.changed = True
        changes.append(
            WallChangeOut(
                label=delta["label"],
                before=before,
                after=after,
                alert_id=link.id if link else None,
                alert_title=link.title if link else None,
            )
        )
    schedule_label = wall_now.get("schedule_as_of")
    basis = wall_now.get("basis") or "consolidated"
    note = (
        f"Debt due by year, from the {basis} schedule as of {schedule_label}, "
        "updated for repayments and new bonds since. Bars split into bonds we "
        "track and the rest reported in the 10-K."
        if schedule_label
        else "No maturity schedule on file yet."
    )

    timeline: list[TimelineItemOut] = []
    for ev in session.scalars(
        select(Event)
        .where(
            Event.issuer_id == issuer.id,
            Event.event_date >= year_start,
            Event.event_date <= point,
            Event.status == "processed",
        )
        .order_by(Event.event_date.asc())
    ).all():
        ev_alerts = session.scalars(select(Alert).where(Alert.event_id == ev.id)).all()
        effect, alert_id = None, None
        for alert in ev_alerts:
            sentence, _, _ = impact_for_alert(session, alert)
            if sentence:
                effect, alert_id = sentence, alert.id
                break
        # Prefer alert title; else the run's logged one-line summary (not boilerplate
        # "press release, 11 May 2026" on Event.summary).
        headline = None
        if ev_alerts:
            headline = ev_alerts[0].title or ev_alerts[0].summary
            effect = effect or ev_alerts[0].summary
        if not headline and ev.run_id:
            run = session.get(AgentRun, ev.run_id)
            if run is not None:
                result = run.result if isinstance(run.result, dict) else {}
                logged = (
                    (result.get("summary") if isinstance(result, dict) else None)
                    or (result.get("log_summary") if isinstance(result, dict) else None)
                )
                if isinstance(logged, str) and logged.strip():
                    headline = logged.strip()
        headline = headline or ev.summary or ev.event_type
        timeline.append(
            TimelineItemOut(
                event_date=ev.event_date,
                title=headline,
                effect=effect,
                alert_id=alert_id or (ev_alerts[0].id if ev_alerts else None),
            )
        )

    return IssuerSnapshotOut(
        issuer=_ref(issuer),
        as_of=point,
        insight=insight,
        insight_pending=insight is None,
        metrics=metric_tiles(session, issuer, point),
        wall=SnapshotWallOut(note=note, buckets=buckets, changes=changes),
        timeline=timeline,
        thresholds=threshold_legend(),
    )
