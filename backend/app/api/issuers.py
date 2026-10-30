"""Issuer list and detail with instruments, walls and event timeline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import HIDDEN_RUN_STATUSES, clamp_limit, json_safe
from app.api.schemas import (
    InstrumentOut,
    IssuerDetailOut,
    IssuerEventOut,
    IssuerListItem,
    IssuerListOut,
    IssuerRef,
)
from app.db import get_session
from app.models import Alert, Event, Instrument, Issuer
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
