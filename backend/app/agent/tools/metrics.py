"""compute_metrics — before/after walls inside a rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.normalize import coerce_amount, parse_rate
from app.agent.run import get_event
from app.agent.tools.base import ok, refuse
from app.models import Instrument, Issuer, StateChange
from app.models.trace import AgentRun
from app.tools.maturity import maturity_wall

# Horizon totals surfaced to the model (materiality also uses these years).
_HORIZON_LABELS = ("FY2026", "FY2027", "FY2028", "FY2029")


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


def _bucket_total(wall: dict[str, Any], label: str) -> Decimal:
    raw = ((wall.get("buckets") or {}).get(label) or {}).get("total_outstanding")
    if raw is None:
        return Decimal(0)
    return Decimal(str(raw))


def _horizon_total(wall: dict[str, Any]) -> str:
    total = Decimal(0)
    for label in _HORIZON_LABELS:
        total += _bucket_total(wall, label)
    return str(total)


def _compact_wall_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Changed buckets (before→after) plus 2026–2029 totals for the model."""
    before_buckets = before.get("buckets") or {}
    after_buckets = after.get("buckets") or {}
    changed: dict[str, Any] = {}
    for label in sorted(set(before_buckets) | set(after_buckets)):
        b = str(_bucket_total(before, label))
        a = str(_bucket_total(after, label))
        if b != a:
            changed[label] = {"before": b, "after": a}
    return {
        "changed_buckets": changed,
        "totals_2026_2029": {
            "before": _horizon_total(before),
            "after": _horizon_total(after),
        },
    }


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
        after = coerce_amount(diff.get("after"))
        if after is None:
            raise ValueError(f"invalid amount in proposed change: {diff.get('after')!r}")
        inst.amount_outstanding = after
        change.status = "applied"
        return
    if action == "add_instrument":
        issuer_id = diff.get("issuer_id")
        if not issuer_id:
            return
        coupon = parse_rate(diff.get("coupon") or "0")
        if coupon is None:
            raise ValueError(f"invalid coupon in proposed change: {diff.get('coupon')!r}")
        amount = coerce_amount(diff.get("amount_outstanding") or "0")
        if amount is None:
            raise ValueError(
                f"invalid amount_outstanding in proposed change: {diff.get('amount_outstanding')!r}"
            )
        session.add(
            Instrument(
                key=diff.get("instrument_key"),
                issuer_id=str(issuer_id),
                name=str(diff.get("name") or "New instrument"),
                coupon=coupon,
                maturity=date.fromisoformat(str(diff["maturity"])),
                maturity_precision=str(diff.get("maturity_precision") or "day"),
                issue_date=(
                    date.fromisoformat(str(diff["issue_date"])) if diff.get("issue_date") else None
                ),
                amount_outstanding=amount,
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

    # Model-facing payload: changed buckets + horizon totals only (full JSON stays on the run).
    compact: dict[str, Any] = {
        "id": output_id,
        "issuer": _compact_wall_delta(before_issuer, after_issuer),
    }
    if parent_id != event.issuer_id:
        compact["group"] = _compact_wall_delta(before_group, after_group)

    return ok(session, run, tool="compute_metrics", arguments=args, result={"metrics": compact})
