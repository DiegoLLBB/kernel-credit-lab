"""Day-by-day replay runner: process every eventful day, resumable, cost-capped."""

from __future__ import annotations

import contextlib
import json
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.loop import (
    TERMINAL_STATUSES,
    RunLimits,
    max_tokens_for_event,
    max_turns_for_event,
    resume_run,
    run_event,
)
from app.agent.run import prepare_rerun, start_agent_run
from app.agent.tools.metrics import compute_metrics
from app.ingest.replay import (
    DEFAULT_REPLAY_PATH,
    ReplayClients,
    load_replay_events,
    step_to_next,
)
from app.models import Event, Filing, Instrument, Issuer, ReplayExpectation, StateChange
from app.models.trace import AgentRun, ModelCall, ToolCall

ClientFactory = Callable[[Event], Any]

QUESTION_PREVIEW_CHARS = 160


class ResponsesClient(Protocol):
    async def responses(self, **kwargs: Any) -> Any: ...


# backend/replay_runs on the host; /app/replay_runs in Docker
DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "replay_runs"


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
    skip_reason: str | None = None
    detail: str | None = None

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
            "skip_reason": self.skip_reason,
            "detail": self.detail,
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
    only_keys: list[str] | None = None
    rerun_keys: list[str] | None = None
    incomplete: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "from": self.window_from.isoformat(),
            "until": self.window_until.isoformat(),
            "only_keys": self.only_keys,
            "rerun_keys": self.rerun_keys,
            "incomplete": self.incomplete,
            "error": self.error,
            "checkpoint": {
                "after_date": self.after_date.isoformat() if self.after_date else None,
                "stopped_reason": self.stopped_reason,
            },
            "events": [e.to_dict() for e in self.events],
            "totals": {
                "events": len([e for e in self.events if not e.skipped]),
                "skipped": len([e for e in self.events if e.skipped]),
                "cost_usd": self.total_cost_usd,
                "tokens": sum(e.tokens for e in self.events if not e.skipped),
                "turns": sum(e.turns for e in self.events if not e.skipped),
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
    """Pending/processing events and paused runs, in calendar then key order.

    Terminal runs (alerted / logged / needs_input / failed) are excluded even when
    ``event.status`` is still ``processing``.
    """
    events = session.scalars(
        select(Event).where(Event.event_date <= until).order_by(Event.event_date, Event.replay_key)
    ).all()
    out: list[Event] = []
    for event in events:
        if _is_paused(session, event):
            out.append(event)
            continue
        if _is_terminal_event(session, event):
            continue
        if event.status in {"pending", "processing"}:
            out.append(event)
    return out


def _selected(event: Event, only_keys: set[str] | None, session: Session | None = None) -> bool:
    if only_keys is None:
        return True
    if (event.replay_key or "") in only_keys:
        return True
    # Merged filing events: match any linked ReplayExpectation key.
    if session is not None:
        from app.models import ReplayExpectation

        linked = session.scalars(
            select(ReplayExpectation.key).where(ReplayExpectation.event_id == event.id)
        ).all()
        return any(k in only_keys for k in linked)
    return False


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


def _instrument_for_maturity_event(session: Session, event: Event) -> Instrument | None:
    """Resolve the instrument for a calendar ``maturity:…`` event."""
    key = event.replay_key or ""
    if key.startswith("maturity:"):
        # maturity:{instrument_key}:{YYYY-MM-DD}
        parts = key.split(":")
        if len(parts) >= 3:
            inst_key = ":".join(parts[1:-1])
            inst = session.scalars(select(Instrument).where(Instrument.key == inst_key)).first()
            if inst is not None:
                return inst
    if event.issuer_id and event.event_date:
        return session.scalars(
            select(Instrument).where(
                Instrument.issuer_id == event.issuer_id,
                Instrument.maturity == event.event_date,
            )
        ).first()
    return None


def complete_maturity_by_code(session: Session, event: Event) -> AgentRun:
    """Apply scheduled maturity without a model call.

    Active instruments get ``mark_matured`` (effective on the maturity date, source
    \"scheduled maturity per Kernel records\"), metrics are recomputed, and the event
    is logged. Already matured / redeemed / repaid instruments log \"already updated\".
    """
    if event.run_id:
        run = session.get(AgentRun, event.run_id)
        if run is not None and run.status in TERMINAL_STATUSES:
            return run
    run = start_agent_run(session, event)
    inst = _instrument_for_maturity_event(session, event)
    already = {"matured", "redeemed", "repaid"}
    if inst is None:
        run.status = "logged"
        run.result = {
            "logged": True,
            "by": "code",
            "reason": "no instrument matched this maturity event",
            "event_type": "maturity",
        }
    elif (inst.status or "").lower() in already:
        run.status = "logged"
        run.result = {
            "logged": True,
            "by": "code",
            "reason": "already updated",
            "event_type": "maturity",
            "instrument_key": inst.key,
            "instrument_status": inst.status,
        }
    else:
        before = inst.status
        effective = inst.maturity or event.event_date
        inst.status = "matured"
        change = StateChange(
            instrument_id=inst.id,
            event_id=event.id,
            run_id=run.id,
            proposed_diff={
                "action": "mark_matured",
                "field": "status",
                "before": before,
                "after": "matured",
                "instrument_key": inst.key,
                "source": "scheduled maturity per Kernel records",
            },
            evidence={"source": "scheduled maturity per Kernel records"},
            status="applied",
            effective_date=effective,
        )
        session.add(change)
        session.flush()
        metrics = compute_metrics(session, run)
        run.status = "logged"
        run.result = {
            "logged": True,
            "by": "code",
            "reason": "scheduled maturity per Kernel records",
            "event_type": "maturity",
            "instrument_key": inst.key,
            "state_change_id": change.id,
            "metrics_ok": bool(metrics.get("ok")),
        }
    run.completed_at = datetime.now(UTC)
    event.status = "processed"
    session.flush()
    return run


def _needs_input_detail(session: Session, run: AgentRun) -> str | None:
    """Best-effort question / refusal text for the run line."""
    meta = run.result or {}
    question = meta.get("question")
    if isinstance(question, str) and question.strip():
        return question.strip()
    # Prefer request_input arguments (any status — refused calls still carry the question).
    for tool_name in ("request_input", "submit_alert"):
        row = session.scalars(
            select(ToolCall)
            .where(ToolCall.run_id == run.id, ToolCall.tool_name == tool_name)
            .order_by(ToolCall.created_at.desc())
        ).first()
        if row is None:
            continue
        args = row.arguments or {}
        if tool_name == "request_input":
            q = args.get("question")
            if isinstance(q, str) and q.strip():
                return q.strip()
            result = row.result or {}
            q2 = result.get("question")
            if isinstance(q2, str) and q2.strip():
                return q2.strip()
        else:
            # submit_alert → needs_input after max refusals: surface the refusal reason.
            err = (row.error or "").strip()
            if err:
                return err
            result = row.result or {}
            reason = result.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
    return None


PERIODIC_FORMS = {"10Q", "10K", "10QA", "10KA"}


def _refresh_metrics_for_event(session: Session, event: Event) -> None:
    """Refresh XBRL credit metrics after a 10-Q/10-K event (best-effort).

    Live mode calls the same helper when it ingests a new 10-Q/10-K. Failures
    (notably no network in tests/offline replay) are logged and swallowed so
    the event outcome is never affected. The filed date is the point-in-time
    cutoff: replay processing in order only sees what was knowable then.
    """
    from app.metrics.refresh import refresh_issuer_metrics

    filings = []
    if event.source_filing_id is not None:
        filing = session.get(Filing, event.source_filing_id)
        if filing is not None:
            filings.append(filing)
    for link in event.event_filings or []:
        if link.filing is not None and all(f.id != link.filing.id for f in filings):
            filings.append(link.filing)
    periodic = [
        f
        for f in filings
        if (f.form_type or "").upper().replace("-", "").replace("/", "") in PERIODIC_FORMS
    ]
    if not periodic:
        return
    issuer_keys: list[str] = []
    for filing in periodic:
        candidates = []
        if filing.issuer is not None and filing.issuer.key:
            candidates.append(filing.issuer.key)
        if event.issuer is not None and event.issuer.key:
            candidates.append(event.issuer.key)
        candidates.extend(exp.issuer_key for exp in event.replay_expectations or [])
        filed_on = filing.filed_at or event.event_date
        for key in dict.fromkeys(candidates):
            if key in issuer_keys:
                continue
            issuer_keys.append(key)
            try:
                stats = refresh_issuer_metrics(session, key, filed_on)
                session.commit()
                print(
                    f"replay: metrics refreshed for {key} as of {filed_on} "
                    f"(+{stats['rows_created']}/~{stats['rows_updated']})"
                )
            except Exception as exc:
                session.rollback()
                print(f"replay: metrics refresh skipped for {key}: {exc}")


def _outcome_from_run(
    session: Session,
    event: Event,
    run: AgentRun,
    *,
    duration_s: float,
    by_code: bool = False,
) -> EventOutcome:
    material: bool | None
    if run.status == "alerted":
        material = True
    elif run.status == "logged":
        material = False
    else:
        material = None
    detail = None
    if run.status == "needs_input":
        detail = _needs_input_detail(session, run)
    elif run.status == "failed":
        detail = (run.error or "").strip() or None
    elif run.status == "paused":
        detail = (run.error or "").strip() or "paused"
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
        detail=detail,
    )


def _skipped_outcome(session: Session, event: Event) -> EventOutcome:
    return EventOutcome(
        replay_key=event.replay_key,
        event_id=event.id,
        event_date=event.event_date.isoformat(),
        issuer=_issuer_label(session, event),
        event_type=event.event_type,
        status="skipped",
        material=None,
        turns=0,
        tokens=0,
        cost_usd=0.0,
        duration_s=0.0,
        run_id=None,
        skipped=True,
        skip_reason="not selected",
        detail="skipped (not selected)",
    )


def format_event_line(outcome: EventOutcome) -> str:
    mat = "—" if outcome.material is None else ("True" if outcome.material else "False")
    issuer = outcome.issuer or "—"
    line = (
        f"{outcome.event_date}\t{issuer}\t{outcome.event_type}\t{outcome.status}\t"
        f"{mat}\t{outcome.turns}\t{outcome.tokens}\t{outcome.cost_usd:.6f}\t"
        f"{outcome.duration_s:.1f}s"
    )
    if outcome.skipped:
        return f"{line}\tskipped (not selected)"
    if outcome.status in {"needs_input", "failed", "paused"} and outcome.detail:
        q = outcome.detail.replace("\n", " ").strip()
        if len(q) > QUESTION_PREVIEW_CHARS:
            q = q[: QUESTION_PREVIEW_CHARS - 1] + "…"
        return f"{line}\t{q}"
    if outcome.status == "needs_input":
        return f"{line}\t(needs_input; question not recorded)"
    return line


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
    day_events = session.scalars(select(Event).where(Event.event_date == latest)).all()
    if day_events and all(_is_terminal_event(session, e) for e in day_events):
        return latest
    return latest - timedelta(days=1)


def _mark_incomplete(result: ReplayResult) -> None:
    if result.stopped_reason and result.stopped_reason != "complete":
        result.incomplete = True


def _mark_event_failed(
    session: Session,
    event: Event,
    exc: BaseException,
    *,
    duration_s: float,
) -> EventOutcome:
    """Persist a failed run for ``event`` and return a log outcome."""
    tb = traceback.format_exc()
    run: AgentRun | None = None
    if event.run_id:
        run = session.get(AgentRun, event.run_id)
    if run is None:
        run = start_agent_run(session, event)
    run.status = "failed"
    run.error = str(exc)
    run.result = {
        **(run.result or {}),
        "error": str(exc),
        "traceback": tb,
        "status": "failed",
    }
    run.completed_at = datetime.now(UTC)
    event.status = "failed"
    session.commit()
    return EventOutcome(
        replay_key=event.replay_key,
        event_id=event.id,
        event_date=event.event_date.isoformat(),
        issuer=_issuer_label(session, event),
        event_type=event.event_type,
        status="failed",
        material=None,
        turns=int(run.turn_count or 0),
        tokens=_run_tokens(session, run),
        cost_usd=float(run.total_cost_usd or 0),
        duration_s=round(duration_s, 3),
        run_id=run.id,
        detail=str(exc),
    )


async def process_one_event(
    session: Session,
    event: Event,
    client: Any,
    *,
    limits: RunLimits | None = None,
    model: str | None = None,
) -> EventOutcome:
    """Run, resume, or code-log a single event (caller handles ``--only`` skips)."""
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
    if event.event_type == "maturity":
        started = time.perf_counter()
        run = complete_maturity_by_code(session, event)
        session.commit()
        return _outcome_from_run(
            session,
            event,
            run,
            duration_s=time.perf_counter() - started,
            by_code=True,
        )

    if limits is None:
        limits = RunLimits(
            max_turns=max_turns_for_event(event, session=session),
            max_tokens=max_tokens_for_event(event, session=session),
        )
    elif limits.max_tokens is None:
        limits = RunLimits(
            max_turns=limits.max_turns,
            max_cost_usd=limits.max_cost_usd,
            max_tokens=max_tokens_for_event(event, session=session),
            max_wall_seconds=limits.max_wall_seconds,
        )
    started = time.perf_counter()
    if _is_paused(session, event) and event.run_id:
        run = await resume_run(session, event.run_id, client, limits, model=model)
    else:
        run = await run_event(session, event.id, client, limits, model=model)
    session.commit()
    outcome = _outcome_from_run(session, event, run, duration_s=time.perf_counter() - started)
    _refresh_metrics_for_event(session, event)
    return outcome


async def run_replay(
    session: Session,
    clients: ReplayClients,
    client_factory: ClientFactory,
    *,
    window_from: date | None = None,
    window_until: date | None = None,
    only_keys: set[str] | None = None,
    rerun_keys: set[str] | None = None,
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
    ``paused`` / ``needs_input`` / ``failed`` are reported and the replay continues;
    only ``max_cost_usd`` or Ctrl+C stops the whole run.

    ``max_cost_usd`` counts only the cost of runs started by this invocation (outcomes
    appended to the log), not historical AgentRun rows already in the database.

    With ``only_keys``, non-matching events stay ``pending`` (logged as skipped) so a
    later full replay can still process them.

    With ``rerun_keys``, prior runs for those replay keys are marked ``superseded``, the
    events are reset to ``pending``, and only those keys are selected (same skip behaviour
    as ``only_keys`` for everything else). Always writes the JSON log (try/finally).
    """
    path = Path(replay_path) if replay_path is not None else DEFAULT_REPLAY_PATH
    load_replay_events(session, path)
    session.commit()

    if rerun_keys:
        prepare_rerun(session, rerun_keys)
        session.commit()
        # Rerun implies selecting only those keys (keep other finished events untouched).
        only_keys = set(rerun_keys) if only_keys is None else (only_keys | set(rerun_keys))

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
        total_cost_usd=0.0,
        only_keys=sorted(only_keys) if only_keys is not None else None,
        rerun_keys=sorted(rerun_keys) if rerun_keys is not None else None,
    )
    _write_log(result)
    handled_ids: set[str] = set()

    try:
        while True:
            if max_cost_usd is not None and result.total_cost_usd >= max_cost_usd:
                result.stopped_reason = "cost_limit"
                break

            unfinished = [
                e for e in _unfinished_events(session, until=until) if e.id not in handled_ids
            ]
            selected = [e for e in unfinished if _selected(e, only_keys, session)]
            not_selected = [e for e in unfinished if not _selected(e, only_keys, session)]

            if selected:
                day = selected[0].event_date
                day_events = [e for e in unfinished if e.event_date == day]

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
                    session.refresh(event)
                    if _is_terminal_event(session, event) and not _is_paused(session, event):
                        handled_ids.add(event.id)
                        continue

                    if not _selected(event, only_keys, session):
                        # Leave pending for a later full replay; only record in the log.
                        outcome = _skipped_outcome(session, event)
                        handled_ids.add(event.id)
                        result.events.append(outcome)
                        result.after_date = day
                        if print_line is not None:
                            print_line(format_event_line(outcome))
                        _write_log(result)
                        continue

                    started = time.perf_counter()
                    event_id = event.id
                    try:
                        client = client_factory(event)
                        outcome = await process_one_event(
                            session,
                            event,
                            client,
                            limits=limits,
                            model=model,
                        )
                    except Exception as exc:
                        # Isolate the failure: mark this event failed and continue.
                        with contextlib.suppress(Exception):
                            session.rollback()
                        failed_event = session.get(Event, event_id) or event
                        outcome = _mark_event_failed(
                            session,
                            failed_event,
                            exc,
                            duration_s=time.perf_counter() - started,
                        )

                    handled_ids.add(event.id)
                    result.events.append(outcome)
                    result.total_cost_usd += float(outcome.cost_usd or 0)
                    result.after_date = day
                    if print_line is not None:
                        print_line(format_event_line(outcome))
                    _write_log(result)
                    if max_cost_usd is not None and result.total_cost_usd >= max_cost_usd:
                        result.stopped_reason = "cost_limit"
                        break
                    # paused / needs_input / failed: report and continue. Only cost_limit
                    # (or Ctrl+C) stops the replay.
                if result.stopped_reason == "cost_limit":
                    break
                continue

            if not_selected:
                # Advance past days that only have non-selected pending events.
                day = not_selected[0].event_date
                for event in [e for e in not_selected if e.event_date == day]:
                    outcome = _skipped_outcome(session, event)
                    handled_ids.add(event.id)
                    result.events.append(outcome)
                    if print_line is not None:
                        print_line(format_event_line(outcome))
                result.after_date = day
                _write_log(result)
                # step_to_next from ``day`` to open later calendar days.
                created = step_to_next(
                    session,
                    until,
                    clients,
                    window_start=start,
                    after=day,
                )
                session.commit()
                if not created:
                    result.stopped_reason = result.stopped_reason or "complete"
                    break
                result.after_date = created[0].event_date - timedelta(days=1)
                _write_log(result)
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
            result.after_date = created[0].event_date - timedelta(days=1)
            _write_log(result)

        if result.stopped_reason is None:
            result.stopped_reason = "complete"
    except KeyboardInterrupt:
        result.stopped_reason = "interrupted"
        result.error = "interrupted by Ctrl+C"
        raise
    except Exception as exc:
        # Unexpected runner-level failure (not per-event).
        result.stopped_reason = "error"
        result.error = str(exc)
    finally:
        _mark_incomplete(result)
        _write_log(result)

    return result
