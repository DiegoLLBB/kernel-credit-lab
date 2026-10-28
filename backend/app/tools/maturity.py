"""Maturity-wall helpers: map instrument maturity dates onto fiscal-year schedule buckets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument, Issuer, MaturitySchedule


def _period_end(bucket: dict[str, Any]) -> date | None:
    raw = bucket.get("period_end")
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def bucket_for(maturity: date, buckets: list[dict[str, Any]]) -> str | None:
    """Return the schedule bucket label whose fiscal range contains ``maturity``.

    Dated buckets are half-open on the left: ``(previous.period_end, this.period_end]``.
    The first dated bucket is open on the left (``(-∞, period_end]``). Dates after the
    last dated ``period_end`` map to the undated "after" bucket when one is present.
    """
    dated: list[tuple[date, dict[str, Any]]] = []
    after: dict[str, Any] | None = None
    for bucket in buckets:
        end = _period_end(bucket)
        if end is None:
            after = bucket
        else:
            dated.append((end, bucket))
    dated.sort(key=lambda item: item[0])

    previous_end: date | None = None
    for end, bucket in dated:
        if (previous_end is None or maturity > previous_end) and maturity <= end:
            label = bucket.get("label")
            return str(label) if label is not None else None
        previous_end = end

    if dated and maturity > dated[-1][0] and after is not None:
        label = after.get("label")
        return str(label) if label is not None else None
    return None


def _subsidiary_ids(session: Session, issuer_id: str) -> set[str]:
    """Return issuer_id plus all descendant issuer ids (holdco → opcos)."""
    ids = {issuer_id}
    frontier = [issuer_id]
    while frontier:
        children = session.scalars(select(Issuer.id).where(Issuer.parent_id.in_(frontier))).all()
        frontier = [cid for cid in children if cid not in ids]
        ids.update(frontier)
    return ids


def _instrument_row(inst: Instrument) -> dict[str, Any]:
    return {
        "id": inst.id,
        "key": inst.key,
        "name": inst.name,
        "issuer_id": inst.issuer_id,
        "maturity": inst.maturity,
        "amount_outstanding": Decimal(inst.amount_outstanding),
        "instrument_type": inst.instrument_type,
        "cusip": inst.cusip,
        "status": inst.status,
    }


def maturity_wall(session: Session, issuer_id: str, as_of: date) -> dict[str, Any]:
    """Two-layer maturity wall for an issuer as of ``as_of``.

    Uses the latest ``MaturitySchedule`` with ``as_of`` on or before the request date.
    Per fiscal-year bucket: 10-K schedule amount, named active instruments in that bucket
    (status ``active`` and maturity ``> as_of``), their sum, unattributed remainder
    (``max(0, schedule - named)``), and a breach flag when named exceeds the 10-K amount.

    Consolidated schedules include subsidiary instruments. Pure DB reads; no model calls.
    """
    schedule = session.scalars(
        select(MaturitySchedule)
        .where(
            MaturitySchedule.issuer_id == issuer_id,
            MaturitySchedule.as_of <= as_of,
        )
        .order_by(MaturitySchedule.as_of.desc())
    ).first()

    result: dict[str, Any] = {
        "issuer_id": issuer_id,
        "as_of": as_of,
        "schedule_id": None,
        "schedule_as_of": None,
        "basis": None,
        "source_url": None,
        "buckets": {},
    }
    if schedule is None:
        return result

    result["schedule_id"] = schedule.id
    result["schedule_as_of"] = schedule.as_of
    result["basis"] = schedule.basis
    result["source_url"] = schedule.source_url

    issuer_ids = {issuer_id}
    if schedule.basis == "consolidated":
        issuer_ids = _subsidiary_ids(session, issuer_id)

    instruments = session.scalars(
        select(Instrument).where(
            Instrument.issuer_id.in_(issuer_ids),
            Instrument.status == "active",
            Instrument.maturity > as_of,
        )
    ).all()

    named_by_label: dict[str, list[Instrument]] = {}
    for inst in instruments:
        label = bucket_for(inst.maturity, schedule.buckets)
        if label is None:
            continue
        named_by_label.setdefault(label, []).append(inst)

    buckets_out: dict[str, Any] = {}
    for bucket in schedule.buckets:
        label = str(bucket.get("label") or "")
        if not label:
            continue
        schedule_amount = Decimal(str(bucket.get("amount") or 0))
        named = sorted(
            named_by_label.get(label, []),
            key=lambda i: (i.maturity, i.key or "", i.id),
        )
        named_sum = sum((Decimal(i.amount_outstanding) for i in named), Decimal("0"))
        breach = named_sum > schedule_amount
        unattributed = max(Decimal("0"), schedule_amount - named_sum)
        buckets_out[label] = {
            "label": label,
            "period_end": _period_end(bucket),
            "schedule_amount": schedule_amount,
            "named_sum": named_sum,
            "unattributed": unattributed,
            "breach": breach,
            "instruments": [_instrument_row(i) for i in named],
        }

    result["buckets"] = buckets_out
    return result
