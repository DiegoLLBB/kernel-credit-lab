"""Read-only access to research runs and per-event agent traces."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import HIDDEN_RUN_STATUSES, prompt_version_for_run
from app.api.schemas import CheckResultOut, RunTraceOut, TraceTurnOut
from app.db import get_session
from app.models import CheckResult, ModelCall, ResearchRun, ToolCall
from app.models.trace import AgentStep

router = APIRouter(prefix="/api", tags=["runs"])
DbSession = Annotated[Session, Depends(get_session)]


class ModelCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    model: str
    task: str | None
    status: str
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    cost_label: str | None = None
    latency_ms: int | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question: str
    workflow: str | None
    mode: str
    status: str
    state: str
    total_cost_usd: Decimal
    total_cost_label: str | None = None
    total_latency_ms: int


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    session: DbSession,
    limit: int = 20,
    include_superseded: bool = False,
) -> list[ResearchRun]:
    stmt = select(ResearchRun).order_by(ResearchRun.created_at.desc())
    if not include_superseded:
        stmt = stmt.where(ResearchRun.status.not_in(sorted(HIDDEN_RUN_STATUSES)))
    stmt = stmt.limit(min(limit, 100))
    return list(session.scalars(stmt))


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str,
    session: DbSession,
    include_superseded: bool = False,
) -> ResearchRun:
    run = session.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if (not include_superseded) and (run.status or "") in HIDDEN_RUN_STATUSES:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/trace", response_model=RunTraceOut)
def get_run_trace(
    run_id: str,
    session: DbSession,
    include_superseded: bool = Query(False),
) -> RunTraceOut:
    run = session.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if (not include_superseded) and (run.status or "") in HIDDEN_RUN_STATUSES:
        raise HTTPException(status_code=404, detail="Run not found")

    steps = session.scalars(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence.asc())
    ).all()
    tool_calls = session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at.asc())
    ).all()
    model_calls = session.scalars(
        select(ModelCall).where(ModelCall.run_id == run.id).order_by(ModelCall.created_at.asc())
    ).all()
    checks = session.scalars(
        select(CheckResult)
        .where(CheckResult.run_id == run.id)
        .order_by(CheckResult.created_at.asc())
    ).all()

    tools_by_step: dict[str, list[ToolCall]] = {}
    for tc in tool_calls:
        if tc.step_id:
            tools_by_step.setdefault(tc.step_id, []).append(tc)
    models_by_step: dict[str, ModelCall] = {}
    for mc in model_calls:
        if mc.step_id and mc.step_id not in models_by_step:
            models_by_step[mc.step_id] = mc

    turns: list[TraceTurnOut] = []
    for i, step in enumerate(steps):
        next_created = steps[i + 1].created_at if i + 1 < len(steps) else None
        step_checks = [
            c
            for c in checks
            if c.created_at >= step.created_at
            and (next_created is None or c.created_at < next_created)
        ]
        commentary = None
        out = step.output or {}
        if isinstance(out, dict):
            commentary = out.get("commentary")
            if commentary is not None:
                commentary = str(commentary)

        primary = (tools_by_step.get(step.id) or [None])[0]
        tool_name = primary.tool_name if primary else None
        arguments = primary.arguments if primary else None
        result: dict[str, Any] | None = None
        refusal: str | None = None
        if primary is not None:
            if primary.status in {"refused", "error"} or primary.error:
                refusal = primary.error or (
                    (primary.result or {}).get("reason")
                    if isinstance(primary.result, dict)
                    else None
                )
                if isinstance(refusal, str):
                    pass
                else:
                    refusal = str(refusal) if refusal else (primary.status or "refused")
                if isinstance(primary.result, dict):
                    result = primary.result
            else:
                result = primary.result if isinstance(primary.result, dict) else None

        mc = models_by_step.get(step.id)
        from app.llm.pricing import cost_label_for_call

        turns.append(
            TraceTurnOut(
                sequence=int(step.sequence),
                commentary=commentary,
                tool=tool_name,
                arguments=arguments,
                result=result,
                refusal_reason=refusal,
                checks=[
                    CheckResultOut(
                        check_name=c.check_name,
                        passed=bool(c.passed),
                        problems=c.problems,
                        attempt=int(c.attempt or 1),
                    )
                    for c in step_checks
                ],
                input_tokens=mc.input_tokens if mc else None,
                output_tokens=mc.output_tokens if mc else None,
                cost_usd=mc.cost_usd if mc else None,
                cost_label=cost_label_for_call(mc.model, mc.cost_usd) if mc else None,
                model=mc.model if mc else None,
                status=step.status,
            )
        )

    meta = run.result or {}
    total_tokens = meta.get("total_tokens") if isinstance(meta, dict) else None
    if total_tokens is not None:
        try:
            total_tokens = int(total_tokens)
        except (TypeError, ValueError):
            total_tokens = None
    if total_tokens is None:
        total_tokens = sum(
            int(m.input_tokens or 0) + int(m.output_tokens or 0) for m in model_calls
        )

    from app.llm.pricing import aggregate_call_costs

    _priced, _unpriced, total_cost_label = aggregate_call_costs(list(model_calls))
    priced_total = _priced if _priced is not None else Decimal(str(run.total_cost_usd or 0))

    return RunTraceOut(
        id=run.id,
        status=run.status,
        prompt_version=prompt_version_for_run(session, run),
        event_id=run.event_id,
        turn_count=int(run.turn_count or len(steps)),
        total_cost_usd=priced_total,
        total_cost_label=total_cost_label,
        total_latency_ms=int(run.total_latency_ms or 0),
        total_tokens=total_tokens,
        turns=turns,
    )


@router.get("/model-calls", response_model=list[ModelCallOut])
def recent_model_calls(session: DbSession, limit: int = 50) -> list[ModelCallOut]:
    from app.llm.pricing import cost_label_for_call

    stmt = select(ModelCall).order_by(ModelCall.created_at.desc()).limit(min(limit, 200))
    rows = list(session.scalars(stmt))
    return [
        ModelCallOut(
            id=r.id,
            provider=r.provider,
            model=r.model,
            task=r.task,
            status=r.status,
            attempts=int(r.attempts or 1),
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
            cost_label=cost_label_for_call(r.model, r.cost_usd),
            latency_ms=r.latency_ms,
        )
        for r in rows
    ]
