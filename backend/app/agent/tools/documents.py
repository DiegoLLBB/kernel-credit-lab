"""list_documents / read_document / search_document — only EventFiling-linked filings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.audit import audit
from app.agent.run import get_event
from app.agent.text import PAGE_CHARS, page_count, page_slice
from app.agent.tools.base import ok, refuse
from app.models import EventFiling, Filing
from app.models.trace import AgentRun

SEARCH_SNIPPET_RADIUS = 200
SEARCH_MAX_HITS = 10


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


def _prior_search_pages(session: Session, run: AgentRun, filing_id: str) -> set[int]:
    """Pages already returned by earlier successful searches on this filing."""
    from app.models import AuditEntry

    pages: set[int] = set()
    entries = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.run_id == run.id, AuditEntry.action == "search_document")
        .order_by(AuditEntry.timestamp)
    ).all()
    for entry in entries:
        detail = entry.detail or {}
        if detail.get("filing_id") != filing_id:
            continue
        for p in detail.get("pages") or []:
            pages.add(int(p))
    return pages


def search_document(
    session: Session,
    run: AgentRun,
    filing_id: str,
    query: str,
) -> dict[str, Any]:
    """Case-insensitive search over stored text. Does not mark pages as read.

    Arguments are ``filing_id`` and ``query`` only. Each search is written to the
    audit log; ``request_input`` counts nothing-new searches when a filing is long.
    """
    args = {"filing_id": filing_id, "query": query}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="search_document", arguments=args, reason=str(exc))

    q = (query or "").strip()
    if not q:
        return refuse(
            session,
            run,
            tool="search_document",
            arguments=args,
            reason="query must be a non-empty string",
        )

    allowed = {f.id for f in _event_filings(session, event.id)}
    if filing_id not in allowed:
        return refuse(
            session,
            run,
            tool="search_document",
            arguments=args,
            reason="filing is not attached to this event",
        )

    filing = session.get(Filing, filing_id)
    if filing is None or not filing.body_text:
        return refuse(
            session,
            run,
            tool="search_document",
            arguments=args,
            reason="filing has no stored text",
        )

    text = filing.body_text
    lower = text.lower()
    needle = q.lower()
    hits: list[dict[str, Any]] = []
    start = 0
    while len(hits) < SEARCH_MAX_HITS:
        idx = lower.find(needle, start)
        if idx < 0:
            break
        snip_start = max(0, idx - SEARCH_SNIPPET_RADIUS)
        snip_end = min(len(text), idx + len(q) + SEARCH_SNIPPET_RADIUS)
        page = idx // PAGE_CHARS
        hits.append(
            {
                "page": page,
                "start": idx,
                "end": idx + len(q),
                "snippet": text[snip_start:snip_end],
            }
        )
        start = idx + max(1, len(q))

    hit_pages = sorted({h["page"] for h in hits})
    prior_pages = _prior_search_pages(session, run, filing_id)
    new_pages = sorted(set(hit_pages) - prior_pages)
    nothing_new = len(new_pages) == 0
    audit(
        session,
        run,
        actor="agent",
        action="search_document",
        detail={
            "filing_id": filing_id,
            "query": q,
            "hit_count": len(hits),
            "pages": hit_pages,
            "new_pages": new_pages,
            "nothing_new": nothing_new,
        },
    )

    return ok(
        session,
        run,
        tool="search_document",
        arguments=args,
        result={
            "filing_id": filing_id,
            "query": q,
            "hit_count": len(hits),
            "hits": hits,
            "pages": hit_pages,
            "new_pages": new_pages,
            "nothing_new": nothing_new,
            "note": "search does not count as reading; call read_document before quoting",
        },
    )
