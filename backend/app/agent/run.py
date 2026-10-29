"""Start and load per-event agent runs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event
from app.models.trace import AgentRun


def abandon_stale_runs(session: Session, event: Event, *, keep_run_id: str | None = None) -> int:
    """Mark older ``running`` runs for ``event`` as ``abandoned``. Returns count abandoned."""
    rows = session.scalars(
        select(AgentRun).where(AgentRun.event_id == event.id, AgentRun.status == "running")
    ).all()
    n = 0
    for row in rows:
        if keep_run_id is not None and row.id == keep_run_id:
            continue
        row.status = "abandoned"
        row.completed_at = datetime.now(UTC)
        row.result = {
            **(row.result or {}),
            "status": "abandoned",
            "reason": "superseded by a new run for this event",
        }
        n += 1
    if n:
        session.flush()
    return n


def supersede_event_runs(session: Session, event: Event) -> int:
    """Mark every prior run for ``event`` as ``superseded`` and reset the event to pending.

    Used by ``run_replay --rerun`` so a fresh agent run can start while keeping other
    events untouched.
    """
    rows = session.scalars(select(AgentRun).where(AgentRun.event_id == event.id)).all()
    n = 0
    now = datetime.now(UTC)
    for row in rows:
        if row.status == "superseded":
            continue
        row.status = "superseded"
        row.completed_at = row.completed_at or now
        row.result = {
            **(row.result or {}),
            "status": "superseded",
            "reason": "rerun requested",
        }
        n += 1
    event.run_id = None
    event.status = "pending"
    session.flush()
    return n


def prepare_rerun(session: Session, keys: set[str]) -> list[Event]:
    """Supersede prior runs for each ``replay_key`` in ``keys``; return matching events."""
    if not keys:
        return []
    events = session.scalars(select(Event).where(Event.replay_key.in_(sorted(keys)))).all()
    for event in events:
        supersede_event_runs(session, event)
    session.flush()
    return list(events)


def start_agent_run(session: Session, event: Event, *, question: str | None = None) -> AgentRun:
    """Create a running agent run linked to ``event``.

    Any prior ``running`` runs for the same event are marked ``abandoned``.
    """
    abandon_stale_runs(session, event)
    run = AgentRun(
        question=question or (event.summary or f"Event {event.id}"),
        workflow="event_monitor",
        mode="standard",
        status="running",
        state="AGENT",
        event_id=event.id,
        turn_count=0,
        refusal_counts={},
        pages_read={},
        coverage_score=None,
        metrics_outputs=[],
    )
    session.add(run)
    session.flush()
    event.run_id = run.id
    event.status = "processing"
    session.flush()
    return run


def get_event(session: Session, run: AgentRun) -> Event:
    if not run.event_id:
        raise ValueError("run has no event_id")
    event = session.get(Event, run.event_id)
    if event is None:
        raise ValueError(f"event {run.event_id} not found")
    return event
