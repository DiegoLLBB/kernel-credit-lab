"""compute_metrics — before/after walls inside a rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.run import get_event
from app.agent.tools.base import ok, refuse
from app.models import Instrument, Issuer, StateChange
from app.models.trace import AgentRun
from app.tools.maturity import maturity_wall


def _serialise_wall(wall: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly copy (dates/Decimals → str)."""

    def conv(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: conv(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [conv(v) for v in obj]
        return obj

    return conv(wall)  # type: ignore[return-value]


def _apply_proposed(session: Session, change: StateChange) -> None:
    """Mutate instruments and mark the change applied (caller must roll back).

    ``instrument_state_at`` uses applied rows with ``effective_date`` so the
    schedule backbone at the 10-K date stays intact while the post-change wall
    is evaluated on/after the effective date.
    """
    diff = change.proposed_diff or {}
    action = diff.get("action")
    if action in {"mark_redeemed", "mark_repaid", "mark_matured"}:
        inst = session.get(Instrument, change.instrument_id) if change.instrument_id else None
        if inst is None:
            return
        inst.status = str(diff.get("after") or "redeemed")
        change.status = "applied"
        return
    if action in {"reduce_outstanding", "update_amount"}:
        inst = session.get(Instrument, change.instrument_id) if change.instrument_id else None
        if inst is None:
            return
        inst.amount_outstanding = Decimal(str(diff.get("after")))
        change.status = "applied"
        return
    if action == "add_instrument":
        issuer_id = diff.get("issuer_id")
        if not issuer_id:
            return
        session.add(
            Instrument(
                key=diff.get("instrument_key"),
                issuer_id=str(issuer_id),
                name=str(diff.get("name") or "New instrument"),
                coupon=Decimal(str(diff.get("coupon") or "0")),
                maturity=date.fromisoformat(str(diff["maturity"])),
                issue_date=(
                    date.fromisoformat(str(diff["issue_date"])) if diff.get("issue_date") else None
                ),
                amount_outstanding=Decimal(str(diff.get("amount_outstanding") or "0")),
                currency=str(diff.get("currency") or "USD"),
                instrument_type=str(diff.get("instrument_type") or "bond"),
                rate_type=str(diff.get("rate_type") or "fixed"),
                status="active",
            )
        )
        change.status = "applied"


def compute_metrics(session: Session, run: AgentRun) -> dict[str, Any]:
    args: dict[str, Any] = {}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="compute_metrics", arguments=args, reason=str(exc))

    if not event.issuer_id:
        return refuse(
            session,
            run,
            tool="compute_metrics",
            arguments=args,
            reason="event has no issuer",
        )

    issuer = session.get(Issuer, event.issuer_id)
    as_of = event.event_date
    parent_id = issuer.parent_id if issuer and issuer.parent_id else event.issuer_id

    before_issuer = _serialise_wall(maturity_wall(session, event.issuer_id, as_of))
    before_group = _serialise_wall(maturity_wall(session, parent_id, as_of))

    proposed = session.scalars(
        select(StateChange).where(StateChange.run_id == run.id, StateChange.status == "proposed")
    ).all()

    after_as_of = as_of
    for change in proposed:
        if change.effective_date and change.effective_date > after_as_of:
            after_as_of = change.effective_date

    # Apply inside a savepoint that is always rolled back.
    after_issuer: dict[str, Any]
    after_group: dict[str, Any]
    nested = session.begin_nested()
    try:
        for change in proposed:
            _apply_proposed(session, change)
        session.flush()
        after_issuer = _serialise_wall(maturity_wall(session, event.issuer_id, after_as_of))
        after_group = _serialise_wall(maturity_wall(session, parent_id, after_as_of))
    finally:
        nested.rollback()

    output_id = str(uuid.uuid4())
    output = {
        "id": output_id,
        "as_of": as_of.isoformat(),
        "after_as_of": after_as_of.isoformat(),
        "issuer_id": event.issuer_id,
        "parent_issuer_id": parent_id,
        "before": {"issuer": before_issuer, "group": before_group},
        "after": {"issuer": after_issuer, "group": after_group},
        "proposed_change_ids": [c.id for c in proposed],
    }
    outputs = list(run.metrics_outputs or [])
    outputs.append(output)
    run.metrics_outputs = outputs
    session.flush()

    return ok(session, run, tool="compute_metrics", arguments=args, result={"metrics": output})
