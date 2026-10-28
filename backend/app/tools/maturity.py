"""Maturity-wall helpers: map instrument maturity dates onto fiscal-year schedule buckets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument, Issuer, MaturitySchedule


class NonUsdAmountError(ValueError):
    """Raised when a non-USD amount would be included in a wall sum (FX not yet supported)."""


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
        "currency": inst.currency,
        "instrument_type": inst.instrument_type,
        "cusip": inst.cusip,
        "status": inst.status,
        "issue_date": inst.issue_date,
    }


def _require_usd(instruments: list[Instrument], *, context: str) -> None:
    for inst in instruments:
        if (inst.currency or "USD").upper() != "USD":
            raise NonUsdAmountError(
                f"{context}: cannot sum non-USD amount for instrument "
                f"{inst.key or inst.id} ({inst.currency}); FX conversion not implemented"
            )


def _sum_amounts(instruments: list[Instrument], *, context: str) -> Decimal:
    _require_usd(instruments, context=context)
    return sum((Decimal(i.amount_outstanding) for i in instruments), Decimal("0"))


def _in_schedule_set(inst: Instrument, schedule_as_of: date) -> bool:
    """Outstanding at the schedule date: issued on/before (or unknown), maturity after."""
    if inst.maturity <= schedule_as_of:
        return False
    return inst.issue_date is None or inst.issue_date <= schedule_as_of


def _instrument_sort_key(inst: Instrument) -> tuple:
    return (inst.maturity, inst.key or "", inst.id)


def _is_named_active(inst: Instrument, as_of: date) -> bool:
    return inst.status == "active" and inst.maturity > as_of


def _is_repaid_since_schedule(inst: Instrument, as_of: date) -> bool:
    """Was in the schedule set but no longer counts as outstanding at ``as_of``."""
    return inst.status != "active" or inst.maturity <= as_of


def _is_new_since_schedule(inst: Instrument, schedule_as_of: date, as_of: date) -> bool:
    return (
        inst.issue_date is not None
        and inst.issue_date > schedule_as_of
        and inst.status == "active"
        and inst.maturity > as_of
    )


def maturity_wall(session: Session, issuer_id: str, as_of: date) -> dict[str, Any]:
    """Two-layer maturity wall for an issuer as of ``as_of`` (CONTEXT.md §8).

    Unattributed remainder and breach are computed against instruments outstanding at
    the schedule's ``as_of`` (issue_date ≤ schedule.as_of or null, maturity > schedule.as_of),
    so the remainder does not grow when a named instrument matures or is repaid.

    Per bucket returns ``named_active``, ``repaid_since_schedule``, ``new_since_schedule``,
    ``unattributed``, and ``total_outstanding`` = named_active + new + unattributed.
    Consolidated schedules include subsidiaries. Raises ``NonUsdAmountError`` if a non-USD
    amount would be summed.
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
    schedule_as_of = schedule.as_of

    issuer_ids = {issuer_id}
    if schedule.basis == "consolidated":
        issuer_ids = _subsidiary_ids(session, issuer_id)

    # All instruments for the issuer scope (status filtered per category below).
    instruments = session.scalars(
        select(Instrument).where(Instrument.issuer_id.in_(issuer_ids))
    ).all()

    # Also pull active post-schedule issuance that may not meet schedule-set filters.
    # (Already included above.)

    by_label_schedule: dict[str, list[Instrument]] = {}
    by_label_new: dict[str, list[Instrument]] = {}
    for inst in instruments:
        label = bucket_for(inst.maturity, schedule.buckets)
        if label is None:
            continue
        if _in_schedule_set(inst, schedule_as_of):
            by_label_schedule.setdefault(label, []).append(inst)
        if _is_new_since_schedule(inst, schedule_as_of, as_of):
            by_label_new.setdefault(label, []).append(inst)

    buckets_out: dict[str, Any] = {}
    for bucket in schedule.buckets:
        label = str(bucket.get("label") or "")
        if not label:
            continue
        schedule_amount = Decimal(str(bucket.get("amount") or 0))
        schedule_named = by_label_schedule.get(label, [])
        new_named = by_label_new.get(label, [])

        named_active = [i for i in schedule_named if _is_named_active(i, as_of)]
        repaid = [i for i in schedule_named if _is_repaid_since_schedule(i, as_of)]

        named_active = sorted(named_active, key=_instrument_sort_key)
        repaid = sorted(repaid, key=_instrument_sort_key)
        new_named = sorted(new_named, key=_instrument_sort_key)

        schedule_sum = _sum_amounts(schedule_named, context=f"bucket {label} schedule set")
        named_active_sum = _sum_amounts(named_active, context=f"bucket {label} named_active")
        new_sum = _sum_amounts(new_named, context=f"bucket {label} new_since_schedule")
        repaid_sum = _sum_amounts(repaid, context=f"bucket {label} repaid_since_schedule")

        breach = schedule_sum > schedule_amount
        unattributed = max(Decimal("0"), schedule_amount - schedule_sum)
        total_outstanding = named_active_sum + new_sum + unattributed

        buckets_out[label] = {
            "label": label,
            "period_end": _period_end(bucket),
            "schedule_amount": schedule_amount,
            "named_active": [_instrument_row(i) for i in named_active],
            "named_active_sum": named_active_sum,
            "repaid_since_schedule": [_instrument_row(i) for i in repaid],
            "repaid_since_schedule_sum": repaid_sum,
            "new_since_schedule": [_instrument_row(i) for i in new_named],
            "new_since_schedule_sum": new_sum,
            "unattributed": unattributed,
            "breach": breach,
            "total_outstanding": total_outstanding,
        }

    result["buckets"] = buckets_out
    return result
