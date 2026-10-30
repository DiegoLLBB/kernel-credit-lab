"""Agent tool: read_chart — SAM+Spark chart reading for presentation / PDF pages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.base import ok, refuse
from app.charts.pipeline import process_page_charts, render_pdf_page
from app.llm.client import LLMClient
from app.models import ChartReading, EventFiling, Filing
from app.models.trace import AgentRun
from app.tools.charts import ChartResult, read_charts


def _row_payload(row: ChartReading) -> dict[str, Any]:
    return {
        "chart_id": row.id,
        "page": row.page,
        "title": row.title,
        "unit": row.unit,
        "categories": list(row.categories or []),
        "values": list(row.values or []),
        "verified": bool(row.verified),
    }


async def read_chart(
    session: Session,
    run: AgentRun,
    filing_id: str,
    page: int | None,
    *,
    client: LLMClient | None = None,
    read_charts_fn: Any | None = None,
) -> dict[str, Any]:
    """Run the chart pipeline on one page; return only verified readings."""
    args = {"filing_id": filing_id, "page": page}
    if not run.event_id:
        return refuse(session, run, tool="read_chart", arguments=args, reason="run has no event_id")

    link = session.scalars(
        select(EventFiling).where(
            EventFiling.event_id == run.event_id, EventFiling.filing_id == filing_id
        )
    ).first()
    if link is None:
        return refuse(
            session,
            run,
            tool="read_chart",
            arguments=args,
            reason="filing is not attached to this event",
        )

    filing = session.get(Filing, filing_id)
    if filing is None:
        return refuse(session, run, tool="read_chart", arguments=args, reason="filing not found")

    page_i = 0 if page is None else int(page)
    if page_i < 0:
        return refuse(session, run, tool="read_chart", arguments=args, reason="page must be ≥ 0")

    form = (filing.form_type or "").lower()
    is_slides = form in {"presentation", "slides", "investor_deck"} or (
        filing.content_bytes is not None and (filing.content_bytes[:5] == b"%PDF-" or False)
    )
    if not is_slides and not filing.content_bytes:
        return refuse(
            session,
            run,
            tool="read_chart",
            arguments=args,
            reason="filing is not a presentation/PDF with stored bytes",
        )

    # Prefer existing verified readings for this page (idempotent / cheap re-call).
    existing = session.scalars(
        select(ChartReading).where(
            ChartReading.filing_id == filing.id,
            ChartReading.page == page_i,
            ChartReading.verified.is_(True),
        )
    ).all()
    if existing:
        return ok(
            session,
            run,
            tool="read_chart",
            arguments=args,
            result={
                "charts": [_row_payload(r) for r in existing],
                "cached": True,
            },
        )

    if not filing.content_bytes:
        return refuse(
            session,
            run,
            tool="read_chart",
            arguments=args,
            reason="filing has no content_bytes to render",
        )

    try:
        page_png = render_pdf_page(filing.content_bytes, page_i)
    except Exception as exc:  # noqa: BLE001
        return refuse(
            session,
            run,
            tool="read_chart",
            arguments=args,
            reason=f"could not render page: {exc}",
        )

    rows = await process_page_charts(
        session,
        filing,
        page_png,
        page_i,
        client=client,
        read_charts_fn=read_charts_fn,
        run_id=run.id,
    )
    verified = [r for r in rows if r.verified]
    return ok(
        session,
        run,
        tool="read_chart",
        arguments=args,
        result={
            "charts": [_row_payload(r) for r in verified],
            "detected": len(rows),
            "verified": len(verified),
            "cached": False,
        },
    )


def make_read_charts_fn(scripted: list[list[ChartResult]]) -> Any:
    """Test helper: return a read_charts_fn that yields successive scripted results."""
    queue = list(scripted)

    async def _fn(**_kwargs: Any) -> list[ChartResult]:
        if not queue:
            return []
        return queue.pop(0)

    return _fn


# Re-export for tests that patch through this module.
__all__ = ["make_read_charts_fn", "read_chart", "read_charts"]
