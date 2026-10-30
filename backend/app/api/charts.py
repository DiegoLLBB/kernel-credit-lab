"""Chart reading API: list by issuer and serve crop/overlay PNGs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.schemas import ChartListOut, ChartReadingOut
from app.db import get_session
from app.models import ChartReading, Filing, Issuer

router = APIRouter(tags=["charts"])
DbSession = Annotated[Session, Depends(get_session)]


def _to_out(
    row: ChartReading, *, issuer_key: str | None, filing_title: str | None
) -> ChartReadingOut:
    return ChartReadingOut(
        id=row.id,
        issuer_key=issuer_key,
        filing_id=row.filing_id,
        filing_title=filing_title,
        page=row.page,
        title=row.title,
        unit=row.unit,
        categories=list(row.categories or []),
        values=list(row.values or []),
        verified=bool(row.verified),
        verification_detail=row.verification_detail,
        bbox=dict(row.bbox or {}),
        has_crop=bool(row.crop_png),
        has_overlay=bool(row.overlay_png),
    )


@router.get("/api/issuers/{key}/charts", response_model=ChartListOut)
def list_issuer_charts(
    key: str,
    session: DbSession,
    verified_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ChartListOut:
    issuer = session.scalars(select(Issuer).where(Issuer.key == key)).first()
    if issuer is None:
        raise HTTPException(status_code=404, detail="Issuer not found")

    q = select(ChartReading).where(ChartReading.issuer_id == issuer.id)
    if verified_only:
        q = q.where(ChartReading.verified.is_(True))
    # Verified first, then page order.
    q = q.order_by(
        case((ChartReading.verified.is_(True), 0), else_=1),
        ChartReading.page.asc(),
        ChartReading.created_at.asc(),
    )
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.scalars(q.offset(offset).limit(limit)).all()
    filing_titles: dict[str, str | None] = {}
    items: list[ChartReadingOut] = []
    for row in rows:
        if row.filing_id not in filing_titles:
            filing = session.get(Filing, row.filing_id)
            filing_titles[row.filing_id] = filing.title if filing else None
        items.append(_to_out(row, issuer_key=issuer.key, filing_title=filing_titles[row.filing_id]))
    return ChartListOut(items=items, total=int(total))


@router.get("/api/charts/{chart_id}/image")
def get_chart_image(
    chart_id: str,
    session: DbSession,
    overlay: bool = Query(False),
) -> Response:
    row = session.get(ChartReading, chart_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Chart not found")
    data = row.overlay_png if overlay else row.crop_png
    if not data:
        raise HTTPException(status_code=404, detail="Chart image not stored")
    return Response(content=bytes(data), media_type="image/png")
