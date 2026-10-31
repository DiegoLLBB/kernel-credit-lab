"""Issuer list and detail with instruments, walls and event timeline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
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
    SnapshotInsightOut,
    SnapshotWallOut,
    TimelineItemOut,
    WallBucketOut,
    WallChangeOut,
)
from app.db import get_session
from app.models import Alert, Event, Instrument, Issuer
from app.models.credit import CreditInsight
from app.models.trace import AgentRun
from app.tools.maturity import instrument_state_at, maturity_wall

router = APIRouter(prefix="/api/issuers", tags=["issuers"])
DbSession = Annotated[Session, Depends(get_session)]


def _ref(issuer: Issuer) -> IssuerRef:
    return IssuerRef(id=issuer.id, key=issuer.key, name=issuer.name)


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
    items: list[IssuerListItem] = []
    for iss in rows:
        parent_key = None
        if iss.parent_id:
            parent = session.get(Issuer, iss.parent_id)
            parent_key = parent.key if parent else None
        sub_count = (
            session.scalar(
                select(func.count()).select_from(Issuer).where(Issuer.parent_id == iss.id)
            )
            or 0
        )
        items.append(
            IssuerListItem(
                id=iss.id,
                key=iss.key,
                name=iss.name,
                ticker=iss.ticker,
                sector=iss.sector,
                parent_key=parent_key,
                subsidiary_count=int(sub_count),
            )
        )
    return IssuerListOut(items=items, total=int(total), limit=limit, offset=offset)


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
        for a in session.scalars(select(Alert).where(Alert.event_id == ev.id)).all():
            if include_superseded or not a.run_id:
                alert_ids.append(a.id)
                continue
            r = session.get(AgentRun, a.run_id)
            if r is None or (r.status or "") not in HIDDEN_RUN_STATUSES:
                alert_ids.append(a.id)
        timeline.append(
            IssuerEventOut(
                id=ev.id,
                replay_key=ev.replay_key,
                event_type=ev.event_type,
                event_date=ev.event_date,
                summary=ev.summary,
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
        select(CreditInsight)
        .where(
            CreditInsight.issuer_id == issuer.id,
            CreditInsight.scope == "snapshot",
            CreditInsight.as_of <= point,
        )
        .order_by(CreditInsight.as_of.desc())
    ).first()
    insight = None
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
        timeline.append(
            TimelineItemOut(
                event_date=ev.event_date,
                title=ev.summary or ev.event_type,
                effect=effect or (ev_alerts[0].summary if ev_alerts else None),
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
