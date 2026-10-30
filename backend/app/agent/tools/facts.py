"""record_fact with quote check against pages this run has read.

Also supports ``fact_type=chart_reading`` citing a verified ChartReading.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.audit import record_check, refusal_count
from app.agent.chart_evidence import chart_reading_verified
from app.agent.normalize import find_quote_offset, quote_has_context
from app.agent.run import get_event
from app.agent.text import page_count, page_slice
from app.agent.tools.base import ok, refuse
from app.models import ChartReading, ChecklistItem, EventFiling, Fact, Filing
from app.models.trace import AgentRun

MAX_FACTS_PER_ITEM = 3
FACT_TYPES = frozenset({"quote", "chart_reading"})


def record_fact(
    session: Session,
    run: AgentRun,
    item_key: str,
    filing_id: str,
    quote: str,
    claim: str,
    *,
    fact_type: str | None = None,
    chart_id: str | None = None,
    category: str | None = None,
    value: float | str | None = None,
) -> dict[str, Any]:
    args = {
        "item_key": item_key,
        "filing_id": filing_id,
        "quote": quote,
        "claim": claim,
        "fact_type": fact_type,
        "chart_id": chart_id,
        "category": category,
        "value": value,
    }
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="record_fact", arguments=args, reason=str(exc))

    kind = (fact_type or "quote").strip() or "quote"
    if kind not in FACT_TYPES:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=f"unknown fact_type {kind!r}; use quote or chart_reading",
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

    if kind == "chart_reading":
        return _record_chart_reading(
            session,
            run,
            item=item,
            filing_id=filing_id,
            claim=claim,
            chart_id=chart_id,
            category=category,
            value=value,
            args=args,
        )

    if not quote_has_context(quote):
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="quote too short; need at least 8 words or 40 characters of context",
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
        fact_type="quote",
        meta=None,
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
            "fact_type": "quote",
        },
    )


def _record_chart_reading(
    session: Session,
    run: AgentRun,
    *,
    item: ChecklistItem,
    filing_id: str,
    claim: str,
    chart_id: str | None,
    category: str | None,
    value: float | str | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    attempt = refusal_count(run, "record_fact") + 1
    if not chart_id or category is None or value is None:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="chart_reading requires chart_id, category, and value",
        )
    try:
        value_dec = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=f"invalid chart value {value!r}",
        )

    chart = session.get(ChartReading, str(chart_id))
    if chart is None:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=f"chart {chart_id!r} not found",
        )
    if chart.filing_id != filing_id:
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="chart does not belong to this filing",
        )

    stub = Fact(
        run_id=run.id,
        checklist_item_id=item.id,
        filing_id=filing_id,
        location=f"chart_reading:{chart_id}",
        exact_quote=f"{category}={value_dec}",
        claim=claim or f"Chart reading {category}={value_dec}",
        fact_type="chart_reading",
        meta={"chart_id": chart_id, "category": category, "value": float(value_dec)},
    )
    ok_chart, problem = chart_reading_verified(session, stub)
    if not ok_chart:
        record_check(
            session,
            run,
            check_name="chart_reading",
            passed=False,
            problems=[problem or "unverified chart"],
            attempt=attempt,
        )
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason=problem or "chart reading is unverified",
        )

    # Category/value must appear on the verified chart.
    cats = [str(c) for c in (chart.categories or [])]
    vals = [float(v) for v in (chart.values or [])]
    matched = False
    for c, v in zip(cats, vals, strict=False):
        if c == str(category) and abs(v - float(value_dec)) <= 1e-6:
            matched = True
            break
    if not matched:
        record_check(
            session,
            run,
            check_name="chart_reading",
            passed=False,
            problems=["category/value not on verified chart"],
            attempt=attempt,
        )
        return refuse(
            session,
            run,
            tool="record_fact",
            arguments=args,
            reason="category/value not found on the verified chart reading",
        )

    record_check(
        session, run, check_name="chart_reading", passed=True, problems=None, attempt=attempt
    )
    session.add(stub)
    if item.status == "open":
        item.status = "proven"
    session.flush()
    return ok(
        session,
        run,
        tool="record_fact",
        arguments=args,
        result={
            "fact_id": stub.id,
            "item_key": item.key,
            "location": stub.location,
            "fact_type": "chart_reading",
            "meta": stub.meta,
        },
    )
