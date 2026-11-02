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


def _per_event_row(session: Session, replay_key: str | None) -> dict[str, Any] | None:
    if not replay_key:
        return None
    row = _latest_agent_eval(session)
    if row is None:
        return None
    for item in row.per_event or []:
        if isinstance(item, dict) and item.get("replay_key") == replay_key:
            return item
    return None


def _expectation_group(session: Session, replay_key: str) -> list[ReplayExpectation]:
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.key == replay_key)
    ).first()
    if exp is None:
        return []
    if exp.event_id:
        return list(
            session.scalars(
                select(ReplayExpectation).where(ReplayExpectation.event_id == exp.event_id)
            ).all()
        )
    return [exp]


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
    replay_key = event.replay_key
    if not replay_key:
        return "not yet scored", _VERDICT_REASONS["not yet scored"]
    pe = _per_event_row(session, replay_key)
    if pe is None:
        return "not yet scored", _VERDICT_REASONS["not yet scored"]

    expected = pe.get("expected_alert") is True
    got = _got_alerted(pe.get("got"))

    if expected and got:
        group = _expectation_group(session, replay_key)
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
