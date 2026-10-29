"""set_checklist with coverage check (public kind rules + silent replay score)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.audit import record_check, refusal_count
from app.agent.checklists import EVENT_KINDS, missing_required
from app.agent.tools.base import ok, refuse
from app.models import ChecklistItem, Event, ReplayExpectation
from app.models.trace import AgentRun

MAX_REFUSALS = 3


def _replay_coverage_score(
    expectation: ReplayExpectation | None, item_keys: set[str]
) -> dict[str, Any] | None:
    """Score checklist against expected_changes without exposing expectation content.

    Never used to refuse; stored on the run for evaluation only (PROGRESS decision 15).
    """
    if expectation is None:
        return None
    expected_actions = []
    for change in expectation.expected_changes or []:
        if isinstance(change, dict) and change.get("action"):
            expected_actions.append(str(change["action"]))
    # Heuristic: each expected action implies the agent should have covered related keys.
    implied: set[str] = set()
    for action in expected_actions:
        if action in {
            "mark_redeemed",
            "mark_repaid",
            "mark_matured",
            "reduce_outstanding",
            "add_instrument",
            "add_loan",
            "add_bond",
        }:
            implied.update({"instrument", "amount", "maturity_effect"})
        elif action != "log_only":
            implied.add("subject")
    if not implied:
        return {
            "score": 1.0,
            "matched": 0,
            "implied_total": 0,
            "expectation_key": expectation.key,
        }
    matched = len(implied & item_keys)
    return {
        "score": matched / len(implied),
        "matched": matched,
        "implied_total": len(implied),
        "expectation_key": expectation.key,
    }


def set_checklist(
    session: Session,
    run: AgentRun,
    kind: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Declare event kind and checklist items; coverage check on required keys."""
    args = {"kind": kind, "items": items}
    if kind not in EVENT_KINDS:
        return refuse(
            session,
            run,
            tool="set_checklist",
            arguments=args,
            reason=f"unknown event kind {kind!r}; choose one of {sorted(EVENT_KINDS)}",
        )
    if not items:
        return refuse(
            session,
            run,
            tool="set_checklist",
            arguments=args,
            reason="checklist items must be a non-empty list",
        )

    keys: set[str] = set()
    normalised: list[dict[str, str]] = []
    for raw in items:
        if not isinstance(raw, dict):
            return refuse(
                session,
                run,
                tool="set_checklist",
                arguments=args,
                reason="each checklist item must be an object",
            )
        key = str(raw.get("key") or "").strip()
        question = str(raw.get("question") or "").strip()
        evidence = str(raw.get("evidence_needed") or raw.get("evidence") or "").strip()
        if not key or not question or not evidence:
            return refuse(
                session,
                run,
                tool="set_checklist",
                arguments=args,
                reason="each item needs key, question and evidence_needed",
            )
        if key in keys:
            return refuse(
                session,
                run,
                tool="set_checklist",
                arguments=args,
                reason=f"duplicate checklist key {key!r}",
            )
        keys.add(key)
        normalised.append({"key": key, "question": question, "evidence_needed": evidence})

    missing = missing_required(kind, keys)
    attempt = refusal_count(run, "set_checklist") + 1

    # Replay score — never refuse on it, never put expectation content in feedback.
    expectation = None
    if run.event_id:
        event = session.get(Event, run.event_id)
        if event is not None:
            expectation = session.scalars(
                select(ReplayExpectation).where(ReplayExpectation.event_id == event.id)
            ).first()
            if expectation is None and event.replay_key:
                expectation = session.scalars(
                    select(ReplayExpectation).where(ReplayExpectation.key == event.replay_key)
                ).first()
    score = _replay_coverage_score(expectation, keys)
    if score is not None:
        run.coverage_score = score

    if missing:
        record_check(
            session,
            run,
            check_name="coverage",
            passed=False,
            problems={"missing": missing},
            attempt=attempt,
        )
        # Refuse up to 3 times; on the next call accept and record the gap.
        if refusal_count(run, "set_checklist") < MAX_REFUSALS:
            return refuse(
                session,
                run,
                tool="set_checklist",
                arguments=args,
                reason=f"coverage check failed; missing required items: {', '.join(missing)}",
                problems=missing,
            )
        record_check(
            session,
            run,
            check_name="coverage_accepted_with_gap",
            passed=True,
            problems={"missing": missing, "accepted_after_refusals": MAX_REFUSALS},
            attempt=attempt,
        )
        gap = missing
    else:
        record_check(
            session, run, check_name="coverage", passed=True, problems=None, attempt=attempt
        )
        gap = []

    session.execute(delete(ChecklistItem).where(ChecklistItem.run_id == run.id))
    for item in normalised:
        session.add(
            ChecklistItem(
                run_id=run.id,
                key=item["key"],
                question=item["question"],
                evidence_needed=item["evidence_needed"],
                status="open",
            )
        )
    run.event_kind = kind
    session.flush()

    result: dict[str, Any] = {
        "kind": kind,
        "items": [i["key"] for i in normalised],
        "accepted_with_gap": gap,
    }
    # Never include coverage_score or expectation fields in the agent-visible result.
    return ok(session, run, tool="set_checklist", arguments=args, result=result)
