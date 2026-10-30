"""End-to-end deck chart pipeline: render → SAM → Spark → verify → store.

See docs/CONTEXT.md Part B §5.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from app.charts.overlay import crop_overlay_png, overlay_png
from app.llm.client import LLMClient
from app.llm.sam import Region
from app.models import ChartReading, Filing
from app.tools.charts import ChartResult, crop, read_charts

logger = logging.getLogger("kernel.charts.pipeline")

DEFAULT_DPI = 150
DEFAULT_MAX_PAGES = 40

SegmentFn = Callable[..., Awaitable[tuple[list[Region], str]]]
ReadChartsFn = Callable[..., Awaitable[list[ChartResult]]]


def render_pdf_page(pdf_bytes: bytes, page_index: int, *, dpi: int = DEFAULT_DPI) -> bytes:
    """Render one 0-based PDF page to PNG bytes via pypdfium2 (~150 dpi default)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"page {page_index} out of range (0..{len(doc) - 1})")
        page = doc[page_index]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.convert("RGB").save(buf, "PNG")
        return buf.getvalue()
    finally:
        doc.close()


def pdf_page_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


def _bbox_dict(box: Region) -> dict[str, Any]:
    return {
        "x1": box.x1,
        "y1": box.y1,
        "x2": box.x2,
        "y2": box.y2,
        "frame_width": box.frame_width,
        "frame_height": box.frame_height,
        "confidence": box.confidence,
    }


def _verification_detail(res: ChartResult) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "status": res.status,
        "reason": res.reason,
        "checks": [
            {
                "category": c.reading.category,
                "series": c.reading.series,
                "read": c.reading.value,
                "implied": c.implied_value,
                "deviation": c.deviation,
                "ok": c.ok,
                "allowed": c.allowed,
            }
            for c in res.checks
        ],
    }
    if res.llm is not None:
        detail["llm"] = {
            "model": res.llm.model,
            "input_tokens": res.llm.input_tokens,
            "output_tokens": res.llm.output_tokens,
            "latency_ms": res.llm.latency_ms,
            "cost_usd": float(res.llm.cost_usd) if res.llm.cost_usd is not None else None,
        }
        logger.info(
            "chart_spark_call",
            extra={
                "model": res.llm.model,
                "input_tokens": res.llm.input_tokens,
                "output_tokens": res.llm.output_tokens,
                "cost_usd": detail["llm"]["cost_usd"],
                "latency_ms": res.llm.latency_ms,
                "status": res.status,
            },
        )
    return detail


def _persist_chart(
    session: Session,
    *,
    filing: Filing,
    page: int,
    page_png: bytes,
    res: ChartResult,
) -> ChartReading:
    image = Image.open(io.BytesIO(page_png))
    crop_bytes = crop(image, res.chart_box)
    checks_ok = [c.ok for c in res.checks] if res.checks else None
    overlay = crop_overlay_png(
        page_png,
        res.chart_box,
        res.bar_boxes,
        checks_ok=checks_ok,
    )
    # Also keep a page-level overlay for debugging when multiple charts share a page.
    _ = overlay_png(page_png, [res])

    ex = res.extraction
    categories = list(ex.categories) if ex else []
    values = [float(v) for v in (ex.values if ex else [])]
    if not categories and res.checks:
        categories = [c.reading.category for c in res.checks]
        values = [float(c.reading.value) for c in res.checks]

    verified = res.status == "verified"
    row = ChartReading(
        issuer_id=filing.issuer_id,
        filing_id=filing.id,
        page=page,
        bbox=_bbox_dict(res.chart_box),
        title=ex.title if ex else None,
        unit=ex.unit if ex else None,
        categories=categories,
        values=values,
        verified=verified,
        verification_detail=_verification_detail(res),
        crop_png=crop_bytes,
        overlay_png=overlay,
    )
    session.add(row)
    session.flush()
    return row


async def process_page_charts(
    session: Session,
    filing: Filing,
    page_png: bytes,
    page_index: int,
    *,
    client: LLMClient | None = None,
    read_charts_fn: ReadChartsFn | None = None,
    run_id: str | None = None,
) -> list[ChartReading]:
    """Run SAM+Spark+verify on one page PNG; persist every reading (verified or not)."""
    close = False
    local = client
    if read_charts_fn is None:
        if local is None:
            local = LLMClient()
            close = True
        assert local is not None

        async def _default_read(**kwargs: Any) -> list[ChartResult]:
            return await read_charts(local, **kwargs)

        read_charts_fn = _default_read

    try:
        results = await read_charts_fn(
            image_bytes=page_png,
            filename=f"page_{page_index}.png",
            run_id=run_id,
        )
    finally:
        if close and local is not None and hasattr(local, "aclose"):
            await local.aclose()

    if not results:
        return []

    rows: list[ChartReading] = []
    for res in results:
        rows.append(
            _persist_chart(session, filing=filing, page=page_index, page_png=page_png, res=res)
        )
    return rows


async def process_filing_charts(
    session: Session,
    filing: Filing,
    *,
    client: LLMClient | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    dpi: int = DEFAULT_DPI,
    read_charts_fn: ReadChartsFn | None = None,
    run_id: str | None = None,
    pdf_bytes: bytes | None = None,
) -> list[ChartReading]:
    """Render up to ``max_pages`` of a presentation Filing and extract charts."""
    data = pdf_bytes if pdf_bytes is not None else filing.content_bytes
    if not data:
        raise ValueError(f"filing {filing.id} has no content_bytes to render")

    n_pages = min(pdf_page_count(data), max_pages)
    all_rows: list[ChartReading] = []
    for page_i in range(n_pages):
        page_png = render_pdf_page(data, page_i, dpi=dpi)
        rows = await process_page_charts(
            session,
            filing,
            page_png,
            page_i,
            client=client,
            read_charts_fn=read_charts_fn,
            run_id=run_id,
        )
        if not rows:
            logger.info("chart_page_skip", extra={"filing_id": filing.id, "page": page_i})
        all_rows.extend(rows)
    return all_rows
