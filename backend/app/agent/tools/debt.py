"""get_issuer_debt — Kernel instrument book and maturity wall as of available_from."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.run import get_event
from app.agent.tools.base import ok, refuse
from app.models import Instrument, Issuer, ReplayExpectation
from app.models.trace import AgentRun
from app.tools.maturity import _subsidiary_ids, instrument_state_at, maturity_wall


def available_from_for_event(session: Session, event: Any) -> date:
    """Discovery date for the event (ReplayExpectation.available_from, else event_date)."""
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.event_id == event.id)
    ).first()
    if exp is None and getattr(event, "replay_key", None):
        exp = session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.key == event.replay_key)
        ).first()
    if exp is not None:
        return exp.available_from
    return event.event_date


def _group_root_id(session: Session, issuer_id: str) -> str:
    issuer = session.get(Issuer, issuer_id)
    root = issuer_id
    seen: set[str] = set()
    while issuer is not None and issuer.parent_id and issuer.parent_id not in seen:
        seen.add(issuer.id)
        root = issuer.parent_id
        issuer = session.get(Issuer, root)
    return root


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _wall_bucket_summary(wall: dict[str, Any]) -> dict[str, Any]:
    """Compact per-bucket view: 10-K amount, named, unattributed, total."""
    buckets: dict[str, Any] = {}
    for label, bucket in (wall.get("buckets") or {}).items():
        buckets[str(label)] = {
            "schedule_amount": str(bucket.get("schedule_amount")),
            "named": str(bucket.get("named_active_sum")),
            "unattributed": str(bucket.get("unattributed")),
            "total": str(bucket.get("total_outstanding")),
        }
    return {
        "issuer_id": wall.get("issuer_id"),
        "as_of": _iso(wall.get("as_of")),
        "schedule_as_of": _iso(wall.get("schedule_as_of")),
        "basis": wall.get("basis"),
        "buckets": buckets,
    }


def get_issuer_debt(session: Session, run: AgentRun) -> dict[str, Any]:
    """Kernel's debt book for the event issuer and its group as of available_from."""
    args: dict[str, Any] = {}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="get_issuer_debt", arguments=args, reason=str(exc))

    if not event.issuer_id:
        return refuse(
            session,
            run,
            tool="get_issuer_debt",
            arguments=args,
            reason="event has no issuer",
        )

    as_of = available_from_for_event(session, event)
    issuer = session.get(Issuer, event.issuer_id)
    root_id = _group_root_id(session, event.issuer_id)
    group_ids = sorted(_subsidiary_ids(session, root_id))

    instruments_out: list[dict[str, Any]] = []
    rows = session.scalars(
        select(Instrument)
        .where(Instrument.issuer_id.in_(group_ids))
        .order_by(Instrument.maturity, Instrument.key)
    ).all()
    for inst in rows:
        if inst.issue_date is not None and inst.issue_date > as_of:
            continue
        amount, status = instrument_state_at(session, inst, as_of)
        instruments_out.append(
            {
                "key": inst.key,
                "name": inst.name,
                "type": inst.instrument_type,
                "coupon": str(inst.coupon),
                "maturity": inst.maturity.isoformat() if inst.maturity else None,
                "maturity_precision": getattr(inst, "maturity_precision", None) or "day",
                "amount_outstanding": str(amount),
                "currency": inst.currency,
                "cusip": inst.cusip,
                "status": status,
                "issuer_id": inst.issuer_id,
            }
        )

    issuer_wall = _wall_bucket_summary(maturity_wall(session, event.issuer_id, as_of))
    group_wall = (
        _wall_bucket_summary(maturity_wall(session, root_id, as_of))
        if root_id != event.issuer_id
        else issuer_wall
    )

    return ok(
        session,
        run,
        tool="get_issuer_debt",
        arguments=args,
        result={
            "as_of": as_of.isoformat(),
            "issuer": (
                {
                    "id": issuer.id,
                    "key": issuer.key,
                    "name": issuer.name,
                }
                if issuer
                else None
            ),
            "group_root_id": root_id,
            "group_issuer_ids": group_ids,
            "instruments": instruments_out,
            "maturity_wall": issuer_wall,
            "group_maturity_wall": group_wall,
        },
    )
