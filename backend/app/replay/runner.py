"""Day-by-day replay runner: process every eventful day, resumable, cost-capped."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.loop import TERMINAL_STATUSES, RunLimits, max_turns_for_event, resume_run, run_event
from app.agent.run import start_agent_run
from app.ingest.replay import (
    DEFAULT_REPLAY_PATH,
    ReplayClients,
    load_replay_events,
    step_to_next,
)
from app.models import Event, Issuer, ReplayExpectation
from app.models.trace import AgentRun, ModelCall

ClientFactory = Callable[[Event], Any]


class ResponsesClient(Protocol):
    async def responses(self, **kwargs: Any) -> Any: ...


DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[3] / "replay_runs"


@dataclass
class EventOutcome:
    replay_key: str | None
    event_id: str
    event_date: str
    issuer: str | None
    event_type: str
    status: str
    material: bool | None
    turns: int
    tokens: int
    cost_usd: float
    duration_s: float
    run_id: str | None
    by_code: bool = False
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_key": self.replay_key,
            "event_id": self.event_id,
            "event_date": self.event_date,
            "issuer": self.issuer,
            "event_type": self.event_type,
            "status": self.status,
            "material": self.material,
            "turns": self.turns,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "duration_s": self.duration_s,
            "run_id": self.run_id,
            "by_code": self.by_code,
            "skipped": self.skipped,
        }


@dataclass
class ReplayResult:
    log_path: Path
    started_at: str
    window_from: date
    window_until: date
    events: list[EventOutcome] = field(default_factory=list)
    after_date: date | None = None
    stopped_reason: str | None = None
    total_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "from": self.window_from.isoformat(),
            "until": self.window_until.isoformat(),
            "checkpoint": {
                "after_date": self.after_date.isoformat() if self.after_date else None,
                "stopped_reason": self.stopped_reason,
            },
            "events": [e.to_dict() for e in self.events],
            "totals": {
                "events": len(self.events),
                "cost_usd": self.total_cost_usd,
                "tokens": sum(e.tokens for e in self.events),
                "turns": sum(e.turns for e in self.events),
            },
            "stopped_reason": self.stopped_reason,
        }


def _issuer_label(session: Session, event: Event) -> str | None:
    if not event.issuer_id:
        return None
    issuer = session.get(Issuer, event.issuer_id)
    return issuer.key if issuer else None


def _run_tokens(session: Session, run: AgentRun) -> int:
    rows = session.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all()
    total = 0
    for row in rows:
        total += int(row.input_tokens or 0) + int(row.output_tokens or 0)
    if total:
        return total
    meta = run.result or {}
    return int(meta.get("total_tokens") or 0)


def _cost_from_db(session: Session) -> Decimal:
    """Sum of AgentRun.total_cost_usd for all event-linked runs."""
    runs = session.scalars(select(AgentRun).where(AgentRun.event_id.is_not(None))).all()
    total = Decimal("0")
    for run in runs:
        total += Decimal(str(run.total_cost_usd or 0))
    return total


def _is_terminal_event(session: Session, event: Event) -> bool:
    if event.status == "processed":
        return True
    if event.status == "failed":
        return True
    if not event.run_id:
        return False
    run = session.get(AgentRun, event.run_id)
    if run is None:
        return False
    return run.status in TERMINAL_STATUSES


def _is_paused(session: Session, event: Event) -> bool:
    if not event.run_id:
        return False
    run = session.get(AgentRun, event.run_id)
    return run is not None and run.status == "paused"


def _unfinished_events(session: Session, *, until: date) -> list[Event]:
    """Pending/processing events and paused runs, in calendar then key order."""
    events = session.scalars(
        select(Event).where(Event.event_date <= until).order_by(Event.event_date, Event.replay_key)
    ).all()
    out: list[Event] = []
    for event in events:
        if _is_paused(session, event):
            out.append(event)
            continue
        if event.status in {"pending", "processing"}:
            out.append(event)
    return out


def complete_rate_update_by_code(session: Session, event: Event) -> AgentRun:
    """Mark a rate_update as logged without calling the model.

    Rate observations already drive metrics; the agent has nothing to extract.
    """
    if event.run_id:
        run = session.get(AgentRun, event.run_id)
        if run is not None and run.status in TERMINAL_STATUSES:
            return run
    run = start_agent_run(session, event)
    run.status = "logged"
    run.result = {
        "logged": True,
        "by": "code",
        "reason": "rate_update refreshes RateObservation-based metrics only",
        "event_type": "rate_update",
    }
    run.completed_at = datetime.now(UTC)
    event.status = "processed"
    session.flush()
    return run


def skip_event_by_filter(session: Session, event: Event) -> AgentRun:
    """Mark a non-matching ``--only`` event processed so the calendar can advance."""
    run = start_agent_run(session, event)
    run.status = "logged"
    run.result = {"logged": True, "skipped_by_filter": True}
    run.completed_at = datetime.now(UTC)
    event.status = "processed"
    session.flush()
    return run


def _outcome_from_run(
    session: Session,
    event: Event,
    run: AgentRun,
    *,
    duration_s: float,
    by_code: bool = False,
    skipped: bool = False,
) -> EventOutcome:
    material: bool | None
    if run.status == "alerted":
        material = True
    elif run.status == "logged":
        material = False
    else:
        material = None
    return EventOutcome(
        replay_key=event.replay_key,
        event_id=event.id,
        event_date=event.event_date.isoformat(),
        issuer=_issuer_label(session, event),
        event_type=event.event_type,
        status=run.status,
        material=material,
        turns=int(run.turn_count or 0),
        tokens=_run_tokens(session, run),
        cost_usd=float(run.total_cost_usd or 0),
        duration_s=round(duration_s, 3),
        run_id=run.id,
        by_code=by_code,
        skipped=skipped,
    )


def format_event_line(outcome: EventOutcome) -> str:
    mat = "—" if outcome.material is None else ("True" if outcome.material else "False")
    issuer = outcome.issuer or "—"
    return (
        f"{outcome.event_date}\t{issuer}\t{outcome.event_type}\t{outcome.status}\t"
        f"{mat}\t{outcome.turns}\t{outcome.tokens}\t{outcome.cost_usd:.6f}\t"
        f"{outcome.duration_s:.1f}s"
    )


def _write_log(result: ReplayResult) -> None:
    result.log_path.parent.mkdir(parents=True, exist_ok=True)
    result.log_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _resume_after_date(session: Session, window_start: date) -> date:
    """Day before the earliest unfinished event, else the latest covered day."""
    unfinished = session.scalars(
        select(Event).where(Event.status.in_(["pending", "processing"]))
    ).all()
    paused_ids = [
        e.id
        for e in session.scalars(select(Event).where(Event.run_id.is_not(None))).all()
        if _is_paused(session, e)
    ]
    dates: list[date] = [e.event_date for e in unfinished]
    for eid in paused_ids:
        ev = session.get(Event, eid)
        if ev is not None:
            dates.append(ev.event_date)
    if dates:
        return min(dates) - timedelta(days=1)
    latest = session.scalar(select(func.max(Event.event_date)))
    if latest is None:
        return window_start - timedelta(days=1)
    # If every event on the latest day is finished, cursor is that day.
    day_events = session.scalars(select(Event).where(Event.event_date == latest)).all()
    if day_events and all(_is_terminal_event(session, e) for e in day_events):
        return latest
    return latest - timedelta(days=1)


async def process_one_event(
    session: Session,
    event: Event,
    client: Any,
    *,
    limits: RunLimits | None = None,
    model: str | None = None,
    only_keys: set[str] | None = None,
) -> EventOutcome:
    """Run, resume, code-log, or skip a single event."""
    if only_keys is not None and (event.replay_key or "") not in only_keys:
        if event.event_type == "rate_update":
            pass  # rate updates always processed by code below
        elif not _is_paused(session, event):
            started = time.perf_counter()
            run = skip_event_by_filter(session, event)
            session.commit()
            return _outcome_from_run(
                session,
                event,
                run,
                duration_s=time.perf_counter() - started,
                skipped=True,
            )

    if event.event_type == "rate_update":
        started = time.perf_counter()
        run = complete_rate_update_by_code(session, event)
        session.commit()
        return _outcome_from_run(
            session,
            event,
            run,
            duration_s=time.perf_counter() - started,
            by_code=True,
        )

    limits = limits or RunLimits(max_turns=max_turns_for_event(event))
    started = time.perf_counter()
    if _is_paused(session, event) and event.run_id:
        run = await resume_run(session, event.run_id, client, limits, model=model)
    else:
        run = await run_event(session, event.id, client, limits, model=model)
    session.commit()
    return _outcome_from_run(session, event, run, duration_s=time.perf_counter() - started)


async def run_replay(
    session: Session,
    clients: ReplayClients,
    client_factory: ClientFactory,
    *,
    window_from: date | None = None,
    window_until: date | None = None,
    only_keys: set[str] | None = None,
    max_cost_usd: float | None = None,
    replay_path: Path | str | None = None,
    runs_dir: Path | None = None,
    log_path: Path | None = None,
    limits: RunLimits | None = None,
    model: str | None = None,
    print_line: Callable[[str], None] | None = print,
) -> ReplayResult:
    """Step day by day with ``step_to_next``, running the agent on each pending event.

    Resumable from DB state: finished events are skipped, paused runs are resumed.
    Stops cleanly when ``max_cost_usd`` is reached (total across event-linked runs).
    """
    path = Path(replay_path) if replay_path is not None else DEFAULT_REPLAY_PATH
    load_replay_events(session, path)
    session.commit()

    with path.open(encoding="utf-8") as fh:
        meta = json.load(fh)
    window = meta.get("window") or {}
    start = window_from or date.fromisoformat(str(window.get("from") or "2026-01-01"))
    until = window_until or date.fromisoformat(str(window.get("to") or "2026-10-27"))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = runs_dir or DEFAULT_RUNS_DIR
    out_path = log_path or (out_dir / f"{stamp}.json")

    result = ReplayResult(
        log_path=out_path,
        started_at=datetime.now(UTC).isoformat(),
        window_from=start,
        window_until=until,
        after_date=_resume_after_date(session, start),
        total_cost_usd=float(_cost_from_db(session)),
    )
    _write_log(result)
    # Never re-process the same event twice in one invocation (paused would loop).
    handled_ids: set[str] = set()

    while True:
        if max_cost_usd is not None and result.total_cost_usd >= max_cost_usd:
            result.stopped_reason = "cost_limit"
            break

        unfinished = [
            e for e in _unfinished_events(session, until=until) if e.id not in handled_ids
        ]

        if unfinished:
            day = unfinished[0].event_date
            day_events = [e for e in unfinished if e.event_date == day]

            # Prefer available_from order via ReplayExpectation when present.
            def _sort_key(ev: Event) -> tuple:
                exp = None
                if ev.replay_key:
                    exp = session.scalars(
                        select(ReplayExpectation).where(ReplayExpectation.key == ev.replay_key)
                    ).first()
                avail = exp.available_from if exp else ev.event_date
                return (avail, ev.replay_key or "")

            day_events.sort(key=_sort_key)
            for event in day_events:
                if max_cost_usd is not None and result.total_cost_usd >= max_cost_usd:
                    result.stopped_reason = "cost_limit"
                    break
                # Refresh in case prior commit expired.
                session.refresh(event)
                if _is_terminal_event(session, event) and not _is_paused(session, event):
                    handled_ids.add(event.id)
                    continue
                client = client_factory(event)
                outcome = await process_one_event(
                    session,
                    event,
                    client,
                    limits=limits,
                    model=model,
                    only_keys=only_keys,
                )
                handled_ids.add(event.id)
                result.events.append(outcome)
                result.total_cost_usd = float(_cost_from_db(session))
                result.after_date = day
                if print_line is not None:
                    print_line(format_event_line(outcome))
                _write_log(result)
                if max_cost_usd is not None and result.total_cost_usd >= max_cost_usd:
                    result.stopped_reason = "cost_limit"
                    break
                # Paused mid-run: stop this invocation; a fresh call resumes.
                if outcome.status == "paused":
                    result.stopped_reason = "paused"
                    break
            if result.stopped_reason in {"cost_limit", "paused"}:
                break
            continue

        created = step_to_next(
            session,
            until,
            clients,
            window_start=start,
            after=result.after_date,
        )
        session.commit()
        if not created:
            result.stopped_reason = result.stopped_reason or "complete"
            break
        # New events will be picked up as unfinished on the next iteration.
        result.after_date = created[0].event_date - timedelta(days=1)
        _write_log(result)

    if result.stopped_reason is None:
        result.stopped_reason = "complete"
    result.total_cost_usd = float(_cost_from_db(session))
    _write_log(result)
    return result
