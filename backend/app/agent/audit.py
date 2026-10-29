"""Append-only audit + tool-call recording for agent tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEntry, CheckResult, ToolCall
from app.models.trace import AgentRun


def audit(
    session: Session,
    run: AgentRun,
    *,
    actor: str,
    action: str,
    detail: dict[str, Any] | None = None,
) -> AuditEntry:
    entry = AuditEntry(run_id=run.id, actor=actor, action=action, detail=detail)
    session.add(entry)
    session.flush()
    return entry


def record_tool_call(
    session: Session,
    run: AgentRun,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any] | None,
    status: str,
    error: str | None = None,
    step_id: str | None = None,
) -> ToolCall:
    row = ToolCall(
        run_id=run.id,
        step_id=step_id,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        status=status,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def record_check(
    session: Session,
    run: AgentRun,
    *,
    check_name: str,
    passed: bool,
    problems: list[Any] | dict[str, Any] | None,
    attempt: int,
) -> CheckResult:
    row = CheckResult(
        run_id=run.id,
        check_name=check_name,
        passed=passed,
        problems=problems,
        attempt=attempt,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        run,
        actor="verification",
        action=f"check:{check_name}",
        detail={"passed": passed, "problems": problems, "attempt": attempt},
    )
    return row


def bump_refusal(run: AgentRun, tool_name: str) -> int:
    counts = dict(run.refusal_counts or {})
    counts[tool_name] = int(counts.get(tool_name, 0)) + 1
    run.refusal_counts = counts
    return counts[tool_name]


def refusal_count(run: AgentRun, tool_name: str) -> int:
    return int((run.refusal_counts or {}).get(tool_name, 0))
