"""request_input — after every page is read, or after enough empty searches on a long filing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.run import get_event
from app.agent.text import page_count
from app.agent.tools.base import ok, refuse
from app.agent.tools.documents import _event_filings
from app.models import AuditEntry, ChecklistItem, Fact, Filing
from app.models.trace import AgentRun

LONG_DOC_PAGES = 20
MIN_EMPTY_SEARCHES = 3


def _nothing_new_search_count(session: Session, run: AgentRun, filing_id: str) -> int:
    """Count searches on ``filing_id`` that added no new hit pages (incl. empty)."""
    entries = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.run_id == run.id, AuditEntry.action == "search_document")
        .order_by(AuditEntry.timestamp)
    ).all()
    count = 0
    for entry in entries:
        detail = entry.detail or {}
        if detail.get("filing_id") != filing_id:
            continue
        if detail.get("nothing_new"):
            count += 1
    return count


def _filing_satisfied(session: Session, run: AgentRun, filing: Filing) -> tuple[bool, str | None]:
    """Return (ok, problem) — problem describes unread pages when not satisfied."""
    text = filing.body_text or ""
    total = page_count(text)
    if total == 0:
        return True, None
    seen = set((run.pages_read or {}).get(filing.id) or [])
    missing = [p for p in range(total) if p not in seen]
    if not missing:
        return True, None
    if total > LONG_DOC_PAGES:
        nn = _nothing_new_search_count(session, run, filing.id)
        if nn >= MIN_EMPTY_SEARCHES:
            return True, None
        return (
            False,
            (
                f"{filing.id}: pages {missing} unread; "
                f"long filing needs all pages read or "
                f"{MIN_EMPTY_SEARCHES} searches that returned nothing new "
                f"(have {nn})"
            ),
        )
    return False, f"{filing.id}: pages {missing}"


def request_input(
    session: Session,
    run: AgentRun,
    item_key: str,
    question: str,
) -> dict[str, Any]:
    args = {"item_key": item_key, "question": question}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="request_input", arguments=args, reason=str(exc))

    item = session.scalars(
        select(ChecklistItem).where(ChecklistItem.run_id == run.id, ChecklistItem.key == item_key)
    ).first()
    if item is None:
        return refuse(
            session,
            run,
            tool="request_input",
            arguments=args,
            reason=f"unknown checklist item {item_key!r}",
        )

    n_facts = session.scalars(select(Fact).where(Fact.checklist_item_id == item.id)).all()
    if n_facts:
        return refuse(
            session,
            run,
            tool="request_input",
            arguments=args,
            reason=f"item {item_key!r} already has a recorded fact",
        )

    filings = _event_filings(session, event.id)
    if not filings:
        return refuse(
            session,
            run,
            tool="request_input",
            arguments=args,
            reason="event has no documents to read",
        )

    unread: list[str] = []
    for filing in filings:
        ok_filing, problem = _filing_satisfied(session, run, filing)
        if not ok_filing and problem:
            unread.append(problem)

    if unread:
        return refuse(
            session,
            run,
            tool="request_input",
            arguments=args,
            reason="not every page of every event document has been read",
            problems=unread,
        )

    item.status = "escalated"
    run.status = "needs_input"
    run.result = {
        **(run.result or {}),
        "item_key": item_key,
        "question": question,
        "status": "needs_input",
    }
    session.flush()
    return ok(
        session,
        run,
        tool="request_input",
        arguments=args,
        result={"item_key": item_key, "question": question, "status": "needs_input"},
    )
