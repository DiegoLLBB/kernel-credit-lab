"""Map replay evaluation rows to human-readable alert verdicts for the dashboard."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.eval.rescore import _agent_group_accurate
from app.models import Alert, Event, ReplayEvaluation, ReplayExpectation

ReplayVerdict = Literal[
    "correct",
    "caught, numbers wrong",
    "false alert",
    "missed",
    "not yet scored",
]

_VERDICT_REASONS: dict[str, str] = {
    "correct": "Replay expected a material alert; Kernel raised one with matching numbers.",
    "caught, numbers wrong": (
        "Kernel alerted but amounts or fiscal years did not match expectations."
    ),
    "false alert": "Kernel alerted when the benchmark expected no material alert.",
    "missed": "Benchmark expected a material alert; Kernel did not raise one.",
    "not yet scored": "Live document or not yet in the latest replay evaluation.",
}


def _latest_agent_eval(session: Session) -> ReplayEvaluation | None:
    return session.scalars(
        select(ReplayEvaluation)
        .where(ReplayEvaluation.kind == "agent")
        .order_by(ReplayEvaluation.evaluated_at.desc())
    ).first()


def _per_event_by_key(session: Session) -> dict[str, dict[str, Any]]:
    row = _latest_agent_eval(session)
    if row is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in row.per_event or []:
        if isinstance(item, dict) and item.get("replay_key"):
            out[str(item["replay_key"])] = item
    return out


def _expectations_for_event(session: Session, event: Event) -> list[ReplayExpectation]:
    """Expectations linked to this event (covers merged ``filing:…`` replay keys)."""
    linked = list(
        session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.event_id == event.id)
        ).all()
    )
    if linked:
        return linked
    if event.replay_key:
        exp = session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.key == event.replay_key)
        ).first()
        if exp is not None:
            if exp.event_id:
                return list(
                    session.scalars(
                        select(ReplayExpectation).where(ReplayExpectation.event_id == exp.event_id)
                    ).all()
                )
            return [exp]
    return []


def _per_event_rows_for_event(
    session: Session, event: Event
) -> tuple[list[dict[str, Any]], list[ReplayExpectation]]:
    """Match eval rows to an event via replay_key and/or linked expectation keys."""
    by_key = _per_event_by_key(session)
    if not by_key:
        return [], []
    group = _expectations_for_event(session, event)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    keys: list[str] = []
    if event.replay_key:
        keys.append(str(event.replay_key))
    keys.extend(exp.key for exp in group if exp.key)
    for key in keys:
        pe = by_key.get(key)
        if pe is not None and key not in seen:
            rows.append(pe)
            seen.add(key)
    return rows, group


def _got_alerted(got: Any) -> bool:
    return str(got or "").startswith("alert")


def replay_verdict_for_event(
    session: Session, event: Event | None
) -> tuple[str | None, str | None]:
    """Return (verdict label, reason) for dashboard badges."""
    if event is None:
        return None, None
    if event.source == "live":
        return "not yet scored", _VERDICT_REASONS["not yet scored"]
    rows, group = _per_event_rows_for_event(session, event)
    if not rows:
        return "not yet scored", _VERDICT_REASONS["not yet scored"]

    # Merged filings: any sibling expected_alert / got drives the badge.
    expected = any(r.get("expected_alert") is True for r in rows) or any(
        bool(exp.expected_alert) for exp in group
    )
    got = any(_got_alerted(r.get("got")) for r in rows)

    if expected and got:
        if group:
            accurate, _detail = _agent_group_accurate(session, group)
        else:
            accurate = True
        if accurate:
            return "correct", _VERDICT_REASONS["correct"]
        return "caught, numbers wrong", _VERDICT_REASONS["caught, numbers wrong"]
    if not expected and got:
        return "false alert", _VERDICT_REASONS["false alert"]
    if expected and not got:
        return "missed", _VERDICT_REASONS["missed"]
    return None, None


def replay_verdict_for_alert(session: Session, alert: Alert) -> tuple[str | None, str | None]:
    event = session.get(Event, alert.event_id) if alert.event_id else None
    return replay_verdict_for_event(session, event)
