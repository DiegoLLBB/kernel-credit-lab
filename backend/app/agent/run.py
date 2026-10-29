"""Start and load per-event agent runs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Event
from app.models.trace import AgentRun


def start_agent_run(session: Session, event: Event, *, question: str | None = None) -> AgentRun:
    """Create a running agent run linked to ``event``."""
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
