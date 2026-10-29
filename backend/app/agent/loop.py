"""Per-event agent loop on Meta ``/responses`` (CONTEXT.md §9.2)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.prompts import PROMPT_VERSION, load_event_prompt
from app.agent.registry import TOOLS, dispatch_tool
from app.agent.run import start_agent_run
from app.agent.tools.base import current_step_id
from app.agent.tools.debt import available_from_for_event
from app.config import get_settings
from app.db import SessionLocal
from app.ingest.replay import agent_visible_payload
from app.llm.client import FunctionCallItem, LLMError, Provider, ResponsesResult
from app.models import Event, Issuer, Watchlist
from app.models.trace import AgentRun, AgentStep, ModelCall

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"alerted", "logged", "needs_input"})
NO_TOOL_NUDGE = "reply with exactly one tool call"
ONE_TOOL_MSG = "one tool per turn"
DEFAULT_MAX_TURNS = 40
DEFAULT_WALL_SECONDS = 600.0
MAX_CONSECUTIVE_NO_TOOL = 3


class ResponsesClient(Protocol):
    async def responses(
        self,
        *,
        provider: Provider = ...,
        model: str | None = ...,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = ...,
        instructions: str | None = ...,
        previous_response_id: str | None = ...,
        tool_choice: Any | None = ...,
        task: str | None = ...,
        prompt_version: str | None = ...,
        run_id: str | None = ...,
        step_id: str | None = ...,
        extra_body: dict[str, Any] | None = ...,
    ) -> ResponsesResult: ...


@dataclass
class RunLimits:
    max_turns: int = DEFAULT_MAX_TURNS
    max_cost_usd: float | None = None
    max_tokens: int | None = None
    max_wall_seconds: float = DEFAULT_WALL_SECONDS


@dataclass
class LoopState:
    run: AgentRun
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tool_choice_used: Any | None = None
    consecutive_no_tool: int = 0
    total_tokens: int = 0
    last_response_id: str | None = None
    started_monotonic: float = 0.0
    started_at_iso: str = ""


def assemble_user_input(session: Session, event: Event) -> str:
    """First user message: agent-visible event + issuer name + watchlist context.

    Never includes ReplayExpectation fields.
    """
    issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
    watch = None
    if event.issuer_id:
        watch = session.scalars(
            select(Watchlist).where(Watchlist.issuer_id == event.issuer_id)
        ).first()
    payload = {
        "event": agent_visible_payload(event),
        "issuer": (
            {
                "id": issuer.id,
                "key": issuer.key,
                "name": issuer.name,
                "ticker": issuer.ticker,
                "sector": issuer.sector,
            }
            if issuer
            else None
        ),
        "watchlist": (
            {"on_watchlist": True, "label": watch.label}
            if watch
            else {"on_watchlist": False, "label": None}
        ),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _run_meta(run: AgentRun) -> dict[str, Any]:
    return dict(run.result or {})


def _save_meta(run: AgentRun, **updates: Any) -> None:
    meta = _run_meta(run)
    meta.update(updates)
    run.result = meta


def _parse_args(arguments: str) -> dict[str, Any]:
    try:
        raw = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _function_call_output(call_id: str, output: dict[str, Any] | str) -> dict[str, Any]:
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def _function_call_item(call: FunctionCallItem) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.call_id,
        "name": call.name,
        "arguments": call.arguments,
    }


def _record_model_call(
    session: Session, run: AgentRun, step: AgentStep, result: ResponsesResult
) -> None:
    """Persist ModelCall in the loop session (AgentStep must already be committed)."""
    session.add(
        ModelCall(
            run_id=run.id,
            step_id=step.id,
            provider=result.provider,
            model=result.model,
            task="event_loop",
            prompt_version=PROMPT_VERSION,
            status="success",
            attempts=result.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )


def _apply_usage(run: AgentRun, state: LoopState, result: ResponsesResult) -> None:
    if result.cost_usd is not None:
        run.total_cost_usd = Decimal(str(run.total_cost_usd or 0)) + Decimal(str(result.cost_usd))
    if result.latency_ms is not None:
        run.total_latency_ms = int(run.total_latency_ms or 0) + int(result.latency_ms)
    turn_tokens = int(result.input_tokens or 0) + int(result.output_tokens or 0)
    state.total_tokens += turn_tokens
    _save_meta(run, total_tokens=state.total_tokens)


def _clean_transcript(transcript: list[Any]) -> list[Any]:
    """Drop internal flags; keep Responses-shaped items for a full-transcript resend."""
    cleaned: list[Any] = []
    for item in transcript:
        if isinstance(item, dict):
            cleaned.append({k: v for k, v in item.items() if not str(k).startswith("_")})
        else:
            cleaned.append(item)
    return cleaned


def _pause(run: AgentRun, reason: str, state: LoopState) -> None:
    run.status = "paused"
    run.error = reason
    _save_meta(
        run,
        pause_reason=reason,
        last_response_id=state.last_response_id,
        transcript=_clean_transcript(state.transcript),
        tool_choice=state.tool_choice_used,
        consecutive_no_tool=state.consecutive_no_tool,
        total_tokens=state.total_tokens,
        started_at=state.started_at_iso,
    )


async def _call_model(
    client: ResponsesClient,
    *,
    model: str,
    instructions: str,
    input_items: list[dict[str, Any]],
    run: AgentRun,
    step_id: str | None,
) -> ResponsesResult:
    """Call ``/responses`` with the full transcript (no previous_response_id)."""
    return await client.responses(
        provider=Provider.META,
        model=model,
        input=input_items,
        tools=TOOLS,
        instructions=instructions,
        previous_response_id=None,
        task="event_loop",
        prompt_version=PROMPT_VERSION,
        run_id=run.id,
        step_id=step_id,
    )


async def _run_loop(
    session: Session,
    state: LoopState,
    client: ResponsesClient,
    limits: RunLimits,
    *,
    model: str,
    instructions: str,
    print_turns: bool = False,
) -> AgentRun:
    run = state.run
    settings = get_settings()
    max_cost = (
        limits.max_cost_usd if limits.max_cost_usd is not None else float(settings.max_run_cost_usd)
    )
    max_tokens = (
        limits.max_tokens if limits.max_tokens is not None else int(settings.max_run_tokens)
    )

    while True:
        if run.status in TERMINAL_STATUSES:
            break

        n_steps = int(
            session.scalar(
                select(func.count()).select_from(AgentStep).where(AgentStep.run_id == run.id)
            )
            or 0
        )
        if n_steps >= limits.max_turns:
            _pause(run, f"turn limit ({limits.max_turns})", state)
            session.commit()
            break

        elapsed = time.monotonic() - state.started_monotonic
        if elapsed >= limits.max_wall_seconds:
            _pause(run, f"wall-clock limit ({limits.max_wall_seconds:.0f}s)", state)
            session.commit()
            break

        if state.total_tokens >= max_tokens:
            _pause(run, f"token ceiling ({max_tokens})", state)
            session.commit()
            break

        cost_so_far = float(run.total_cost_usd or 0)
        if cost_so_far >= max_cost:
            _pause(run, f"cost ceiling (${max_cost})", state)
            session.commit()
            break

        sequence = n_steps + 1
        step = AgentStep(
            run_id=run.id,
            sequence=sequence,
            state="AGENT",
            status="running",
            input_summary=f"transcript_items={len(state.transcript)}",
        )
        session.add(step)
        # Commit before the model call so ModelCall.step_id FK is valid.
        session.commit()

        model_input = _clean_transcript(state.transcript)
        token = current_step_id.set(step.id)
        try:
            result = await _call_model(
                client,
                model=model,
                instructions=instructions,
                input_items=model_input,
                run=run,
                step_id=step.id,
            )
        except LLMError as exc:
            step.status = "error"
            step.output_summary = str(exc)[:500]
            _pause(run, f"API failure: {exc}", state)
            session.commit()
            if print_turns:
                print(f"turn={sequence} tool=None result=error status=paused")
            break
        finally:
            with contextlib.suppress(ValueError):
                current_step_id.reset(token)

        state.last_response_id = result.id or state.last_response_id
        state.tool_choice_used = result.tool_choice
        _apply_usage(run, state, result)
        _record_model_call(session, run, step, result)

        calls = list(result.function_calls)
        step.output = {
            "response_id": result.id,
            "commentary": result.commentary,
            "tool_choice": result.tool_choice,
            "function_calls": [
                {"call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in calls
            ],
        }
        step.latency_ms = result.latency_ms

        tool_name: str | None = None
        tool_result: dict[str, Any] | None = None

        if not calls:
            state.consecutive_no_tool += 1
            state.transcript.append({"type": "message", "role": "user", "content": NO_TOOL_NUDGE})
            step.status = "no_tool"
            step.output_summary = NO_TOOL_NUDGE
            step.output = {**(step.output or {}), "nudge": NO_TOOL_NUDGE}
            _save_meta(
                run,
                last_response_id=state.last_response_id,
                transcript=_clean_transcript(state.transcript),
                tool_choice=state.tool_choice_used,
                consecutive_no_tool=state.consecutive_no_tool,
                total_tokens=state.total_tokens,
                started_at=state.started_at_iso,
            )
            session.commit()
            if print_turns:
                print(f"turn={sequence} tool=None result={NO_TOOL_NUDGE!r} status={run.status}")
            if state.consecutive_no_tool >= MAX_CONSECUTIVE_NO_TOOL:
                reason = f"{MAX_CONSECUTIVE_NO_TOOL} consecutive turns without a tool call"
                _pause(run, reason, state)
                session.commit()
                if print_turns:
                    print(f"turn={sequence} tool=None result=paused status=paused")
                break
            if state.total_tokens >= max_tokens:
                _pause(run, f"token ceiling ({max_tokens})", state)
                session.commit()
                break
            continue

        state.consecutive_no_tool = 0
        primary, *extras = calls

        state.transcript.append(_function_call_item(primary))
        for extra in extras:
            state.transcript.append(_function_call_item(extra))

        args = _parse_args(primary.arguments)
        tool_name = primary.name
        token = current_step_id.set(step.id)
        try:
            tool_result = dispatch_tool(session, run, primary.name, args)
        finally:
            current_step_id.reset(token)

        note = getattr(client, "note_tool_result", None)
        if callable(note):
            note(tool_result, item_key=args.get("item_key"))

        state.transcript.append(_function_call_output(primary.call_id, tool_result))
        for extra in extras:
            refused = {
                "ok": False,
                "refused": True,
                "reason": ONE_TOOL_MSG,
                "problems": [ONE_TOOL_MSG],
            }
            state.transcript.append(_function_call_output(extra.call_id, refused))

        step.status = "ok" if tool_result.get("ok") else "refused"
        if tool_result.get("ok"):
            step.output_summary = f"{tool_name}: ok"
        else:
            step.output_summary = f"{tool_name}: {tool_result.get('reason', 'refused')}"
        step.output = {
            **(step.output or {}),
            "tool": tool_name,
            "arguments": args,
            "result": tool_result,
            "extra_calls_refused": len(extras),
        }

        _save_meta(
            run,
            last_response_id=state.last_response_id,
            transcript=_clean_transcript(state.transcript),
            tool_choice=state.tool_choice_used,
            consecutive_no_tool=0,
            total_tokens=state.total_tokens,
            started_at=state.started_at_iso,
        )
        session.commit()
        session.refresh(run)

        if print_turns:
            brief = {
                "ok": tool_result.get("ok"),
                "refused": tool_result.get("refused"),
                "reason": tool_result.get("reason"),
                "status": tool_result.get("status"),
            }
            print(f"turn={sequence} tool={tool_name} result={brief} status={run.status}")

        if run.status in TERMINAL_STATUSES:
            break

        if state.total_tokens >= max_tokens:
            _pause(run, f"token ceiling ({max_tokens})", state)
            session.commit()
            break

        if float(run.total_cost_usd or 0) >= max_cost:
            _pause(run, f"cost ceiling (${max_cost})", state)
            session.commit()
            break

    session.expire(run, ["steps"])
    session.refresh(run)
    return run


def _init_state(run: AgentRun, user_input: str, *, resume: bool) -> LoopState:
    meta = _run_meta(run)
    now_iso = datetime.now(UTC).isoformat()
    if resume:
        transcript = _clean_transcript(list(meta.get("transcript") or []))
        started_at = meta.get("started_at") or now_iso
        try:
            started_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            elapsed = (datetime.now(UTC) - started_dt).total_seconds()
            started_monotonic = time.monotonic() - max(0.0, elapsed)
        except ValueError:
            started_monotonic = time.monotonic()
            started_at = now_iso
        return LoopState(
            run=run,
            transcript=transcript,
            tool_choice_used=meta.get("tool_choice"),
            consecutive_no_tool=int(meta.get("consecutive_no_tool") or 0),
            total_tokens=int(meta.get("total_tokens") or 0),
            last_response_id=meta.get("last_response_id"),
            started_monotonic=started_monotonic,
            started_at_iso=str(started_at),
        )

    transcript = [{"type": "message", "role": "user", "content": user_input}]
    return LoopState(
        run=run,
        transcript=transcript,
        started_monotonic=time.monotonic(),
        started_at_iso=now_iso,
    )


async def run_event(
    session: Session,
    event_id: str,
    client: ResponsesClient,
    limits: RunLimits | None = None,
    *,
    model: str | None = None,
    print_turns: bool = False,
) -> AgentRun:
    """Start (or continue a fresh) agent run for ``event_id`` until terminal or paused."""
    limits = limits or RunLimits()
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError(f"event {event_id} not found")

    run = start_agent_run(session, event)
    session.commit()

    user_input = assemble_user_input(session, event)
    as_of = available_from_for_event(session, event)
    instructions = load_event_prompt(today=as_of)
    settings = get_settings()
    model_id = model or settings.default_model or "muse-spark-1.3"

    state = _init_state(run, user_input, resume=False)
    _save_meta(
        run,
        transcript=state.transcript,
        started_at=state.started_at_iso,
        user_input=user_input,
        prompt_version=PROMPT_VERSION,
        available_from=as_of.isoformat(),
    )
    session.commit()

    return await _run_loop(
        session,
        state,
        client,
        limits,
        model=model_id,
        instructions=instructions,
        print_turns=print_turns,
    )


async def resume_run(
    session: Session,
    run_id: str,
    client: ResponsesClient,
    limits: RunLimits | None = None,
    *,
    model: str | None = None,
    print_turns: bool = False,
) -> AgentRun:
    """Continue a paused run from the last saved turn."""
    limits = limits or RunLimits()
    run = session.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status in TERMINAL_STATUSES:
        return run

    run.status = "running"
    run.error = None
    session.commit()

    meta = _run_meta(run)
    user_input = meta.get("user_input") or ""
    if not user_input and run.event_id:
        event = session.get(Event, run.event_id)
        if event:
            user_input = assemble_user_input(session, event)

    state = _init_state(run, user_input, resume=True)
    if not state.transcript and user_input:
        state.transcript = [{"type": "message", "role": "user", "content": user_input}]

    as_of = meta.get("available_from")
    if as_of is None and run.event_id:
        event = session.get(Event, run.event_id)
        if event is not None:
            as_of = available_from_for_event(session, event)
    instructions = load_event_prompt(today=as_of)
    settings = get_settings()
    model_id = model or settings.default_model or "muse-spark-1.3"

    return await _run_loop(
        session,
        state,
        client,
        limits,
        model=model_id,
        instructions=instructions,
        print_turns=print_turns,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the per-event agent loop")
    parser.add_argument("--event", required=True, help="Event id")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use built-in no-op FakeModel (demo only)",
    )
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume a paused run instead")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    args = parser.parse_args(argv)

    limits = RunLimits(max_turns=args.max_turns)

    async def _amain() -> AgentRun:
        if args.fake:
            from app.agent.fake import FakeModel, plain_text

            # Demo fake just pauses after three plain texts unless a richer script is wired.
            client: ResponsesClient = FakeModel([plain_text("…")] * 3)
        else:
            from app.llm import LLMClient

            client = LLMClient()

        with SessionLocal() as session:
            try:
                if args.resume:
                    run = await resume_run(session, args.resume, client, limits, print_turns=True)
                else:
                    run = await run_event(session, args.event, client, limits, print_turns=True)
                print(f"final status={run.status} run_id={run.id}")
                return run
            finally:
                close = getattr(client, "aclose", None)
                if close:
                    await close()

    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
