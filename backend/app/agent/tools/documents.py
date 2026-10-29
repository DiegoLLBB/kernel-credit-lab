"""list_documents / read_document — only EventFiling-linked filings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.run import get_event
from app.agent.text import PAGE_CHARS, page_count, page_slice
from app.agent.tools.base import ok, refuse
from app.models import EventFiling, Filing
from app.models.trace import AgentRun


def _event_filings(session: Session, event_id: str) -> list[Filing]:
    links = session.scalars(select(EventFiling).where(EventFiling.event_id == event_id)).all()
    filings: list[Filing] = []
    for link in links:
        filing = session.get(Filing, link.filing_id)
        if filing is not None:
            filings.append(filing)
    return filings


def list_documents(session: Session, run: AgentRun) -> dict[str, Any]:
    args: dict[str, Any] = {}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="list_documents", arguments=args, reason=str(exc))

    docs = []
    for filing in _event_filings(session, event.id):
        text = filing.body_text or ""
        docs.append(
            {
                "filing_id": filing.id,
                "title": filing.title,
                "form_type": filing.form_type,
                "url": filing.url,
                "filed_at": filing.filed_at.isoformat() if filing.filed_at else None,
                "pages": page_count(text),
                "chars": len(text),
            }
        )
    return ok(session, run, tool="list_documents", arguments=args, result={"documents": docs})


def read_document(
    session: Session,
    run: AgentRun,
    filing_id: str,
    page: int | None = None,
) -> dict[str, Any]:
    args = {"filing_id": filing_id, "page": page}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="read_document", arguments=args, reason=str(exc))

    allowed = {f.id for f in _event_filings(session, event.id)}
    if filing_id not in allowed:
        return refuse(
            session,
            run,
            tool="read_document",
            arguments=args,
            reason="filing is not attached to this event",
        )

    filing = session.get(Filing, filing_id)
    if filing is None or not filing.body_text:
        return refuse(
            session,
            run,
            tool="read_document",
            arguments=args,
            reason="filing has no stored text",
        )

    text = filing.body_text
    pages = page_count(text)
    page_i = 0 if page is None else int(page)
    if page_i < 0 or page_i >= pages:
        return refuse(
            session,
            run,
            tool="read_document",
            arguments=args,
            reason=f"page {page_i} out of range (0..{pages - 1})",
        )

    chunk, start, end = page_slice(text, page_i)
    read = dict(run.pages_read or {})
    seen = set(read.get(filing_id) or [])
    seen.add(page_i)
    read[filing_id] = sorted(seen)
    run.pages_read = read
    session.flush()

    return ok(
        session,
        run,
        tool="read_document",
        arguments=args,
        result={
            "filing_id": filing_id,
            "page": page_i,
            "pages": pages,
            "page_chars": PAGE_CHARS,
            "start": start,
            "end": end,
            "text": chunk,
        },
    )
