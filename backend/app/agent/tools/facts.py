"""record_fact with quote check against pages this run has read."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.audit import record_check, refusal_count
from app.agent.normalize import find_quote_offset, quote_has_context
from app.agent.run import get_event
from app.agent.text import page_count, page_slice
from app.agent.tools.base import ok, refuse
from app.models import ChecklistItem, EventFiling, Fact, Filing
from app.models.trace import AgentRun

MAX_FACTS_PER_ITEM = 3


def record_fact(
    session: Session,
    run: AgentRun,
    item_key: str,
    filing_id: str,
    quote: str,
    claim: str,
) -> dict[str, Any]:
    args = {
        "item_key": item_key,
        "filing_id": filing_id,
        "quote": quote,
        "claim": claim,
    }
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="record_fact", arguments=args, reason=str(exc))

    if not quote_has_context(quote):
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="quote too short; need at least 8 words or 40 characters of context",
        )

    item = session.scalars(
        select(ChecklistItem).where(ChecklistItem.run_id == run.id, ChecklistItem.key == item_key)
    ).first()
    if item is None:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=f"unknown checklist item {item_key!r}",
        )

    n_facts = (
        session.scalar(
            select(func.count()).select_from(Fact).where(Fact.checklist_item_id == item.id)
        )
        or 0
    )
    if int(n_facts) >= MAX_FACTS_PER_ITEM:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=f"at most {MAX_FACTS_PER_ITEM} facts per item",
        )

    link = session.scalars(
        select(EventFiling).where(
            EventFiling.event_id == event.id, EventFiling.filing_id == filing_id
        )
    ).first()
    if link is None:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="filing is not attached to this event",
        )

    filing = session.get(Filing, filing_id)
    if filing is None or not filing.body_text:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="filing has no stored text",
        )

    read_pages = set((run.pages_read or {}).get(filing_id) or [])
    if not read_pages:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="quote not found on that page",
        )

    text = filing.body_text
    matched_page: int | None = None
    offset: int | None = None
    for page_i in sorted(read_pages):
        if page_i < 0 or page_i >= page_count(text):
            continue
        chunk, start, _end = page_slice(text, page_i)
        local = find_quote_offset(chunk, quote)
        if local is not None:
            matched_page = page_i
            offset = start + local
            break

    attempt = refusal_count(run, "record_fact") + 1
    if matched_page is None or offset is None:
        record_check(
            session,
            run,
            check_name="quote",
            passed=False,
            problems=["quote not found on that page"],
            attempt=attempt,
        )
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="quote not found on that page",
        )

    record_check(session, run, check_name="quote", passed=True, problems=None, attempt=attempt)
    fact = Fact(
        run_id=run.id,
        checklist_item_id=item.id,
        filing_id=filing_id,
        location=f"page={matched_page},offset={offset}",
        exact_quote=quote,
        claim=claim,
    )
    session.add(fact)
    if item.status == "open":
        item.status = "proven"
    session.flush()

    return ok(
        session,
        run,
        tool="record_fact",
        arguments=args,
        result={
            "fact_id": fact.id,
            "item_key": item_key,
            "location": fact.location,
        },
    )
