"""Shared tool result helpers."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from app.agent.audit import audit, bump_refusal, record_tool_call
from app.models.trace import AgentRun

# Set by the agent loop so ToolCall rows link to the current AgentStep.
current_step_id: ContextVar[str | None] = ContextVar("agent_current_step_id", default=None)


def ok(
    session: Session,
    run: AgentRun,
    *,
    tool: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    run.turn_count = int(run.turn_count or 0) + 1
    payload = {"ok": True, **result}
    record_tool_call(
        session,
        run,
        tool_name=tool,
        arguments=arguments,
        result=payload,
        status="success",
        step_id=current_step_id.get(),
    )
    audit(session, run, actor="agent", action=f"tool:{tool}", detail={"arguments": arguments})
    session.flush()
    return payload


def refuse(
    session: Session,
    run: AgentRun,
    *,
    tool: str,
    arguments: dict[str, Any],
    reason: str,
    problems: list[str] | None = None,
) -> dict[str, Any]:
    run.turn_count = int(run.turn_count or 0) + 1
    count = bump_refusal(run, tool)
    payload = {
        "ok": False,
        "refused": True,
        "reason": reason,
        "problems": problems or [reason],
        "refusal_count": count,
    }
    record_tool_call(
        session,
        run,
        tool_name=tool,
        arguments=arguments,
        result=payload,
        status="refused",
        error=reason,
        step_id=current_step_id.get(),
    )
    audit(
        session,
        run,
        actor="agent",
        action=f"tool:{tool}:refused",
        detail={"reason": reason, "problems": problems, "refusal_count": count},
    )
    session.flush()
    return payload
