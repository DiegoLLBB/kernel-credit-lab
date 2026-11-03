"""Upcoming named-instrument maturities across the watchlist."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.credit import metric_tiles
from app.api.schemas import IssuerRef
from app.db import get_session
from app.models import Instrument, Issuer
from app.tools.maturity import instrument_state_at

router = APIRouter(prefix="/api/maturities", tags=["maturities"])
DbSession = Annotated[Session, Depends(get_session)]


class UpcomingInstrumentOut(BaseModel):
    issuer: IssuerRef
    instrument_key: str | None = None
    instrument_name: str
    instrument_type: str
    amount: Decimal
    currency: str = "USD"
    maturity_date: date
    status: str


class IssuerDue12mOut(BaseModel):
    issuer: IssuerRef
    due_12m: Decimal | None = None
    period_end: date | None = None
    missing_reason: str | None = None
    source_tag: str | None = None


class UpcomingMaturitiesOut(BaseModel):
    as_of: date
    months: int
    until: date
    instruments: list[UpcomingInstrumentOut] = Field(default_factory=list)
    issuer_due_12m: list[IssuerDue12mOut] = Field(default_factory=list)


def _issuer_ref(iss: Issuer) -> IssuerRef:
    return IssuerRef(id=iss.id, key=iss.key, name=iss.name)


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@router.get("/upcoming", response_model=UpcomingMaturitiesOut)
def upcoming_maturities(
    session: DbSession,
    months: int = Query(12, ge=1, le=60),
    as_of: date | None = None,
) -> UpcomingMaturitiesOut:
    """Named bonds/loans maturing within ``months``, plus each root's XBRL due_12m."""
    point = as_of or date.today()
    until = _add_months(point, months)

    instruments_out: list[UpcomingInstrumentOut] = []
    inst_rows = session.scalars(select(Instrument).order_by(Instrument.maturity.asc())).all()
    issuer_cache: dict[str, Issuer] = {}
    for inst in inst_rows:
        if inst.maturity <= point or inst.maturity > until:
            continue
        amount, status = instrument_state_at(session, inst, point)
        if status != "active" or amount <= 0:
            continue
        iss = issuer_cache.get(inst.issuer_id)
        if iss is None:
            iss = session.get(Issuer, inst.issuer_id)
            if iss is None:
                continue
            issuer_cache[inst.issuer_id] = iss
        instruments_out.append(
            UpcomingInstrumentOut(
                issuer=_issuer_ref(iss),
                instrument_key=inst.key,
                instrument_name=inst.name,
                instrument_type=inst.instrument_type,
                amount=amount,
                currency=inst.currency or "USD",
                maturity_date=inst.maturity,
                status=status,
            )
        )
    instruments_out.sort(key=lambda r: (r.maturity_date, r.issuer.key or "", r.instrument_name))

    due_rows: list[IssuerDue12mOut] = []
    roots = session.scalars(
        select(Issuer).where(Issuer.parent_id.is_(None)).order_by(Issuer.key.asc())
    ).all()
    for iss in roots:
        tiles = metric_tiles(
            session,
            iss,
            point,
            only=("due_12m",),
            include_peers=False,
            include_sparkline=False,
        )
        tile = tiles[0] if tiles else None
        due_rows.append(
            IssuerDue12mOut(
                issuer=_issuer_ref(iss),
                due_12m=tile.value if tile else None,
                period_end=tile.period_end if tile else None,
                missing_reason=tile.missing_reason if tile else "no data yet",
                source_tag=tile.source_tag if tile else None,
            )
        )

    return UpcomingMaturitiesOut(
        as_of=point,
        months=months,
        until=until,
        instruments=instruments_out,
        issuer_due_12m=due_rows,
    )
