"""request_input — only after every page of every event document has been read."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.run import get_event
from app.agent.text import page_count
from app.agent.tools.base import ok, refuse
from app.agent.tools.documents import _event_filings
from app.models import ChecklistItem, Fact
from app.models.trace import AgentRun


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
    pages_read = run.pages_read or {}
    for filing in filings:
        text = filing.body_text or ""
        total = page_count(text)
        seen = set(pages_read.get(filing.id) or [])
        missing = [p for p in range(total) if p not in seen]
        if missing:
            unread.append(f"{filing.id}: pages {missing}")

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
    session.flush()
    return ok(
        session,
        run,
        tool="request_input",
        arguments=args,
        result={"item_key": item_key, "question": question, "status": "needs_input"},
    )
