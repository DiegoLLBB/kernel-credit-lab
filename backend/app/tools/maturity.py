"""Maturity-wall helpers: map instrument maturity dates onto fiscal-year schedule buckets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument, Issuer, MaturitySchedule, StateChange
from app.tools.fx import FxRateMissingError, amount_to_usd


class NonUsdAmountError(ValueError):
    """Raised when a non-USD amount cannot be converted (no FX series / rate)."""


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


def instrument_state_at(session: Session, instrument: Instrument, d: date) -> tuple[Decimal, str]:
    """Amount and status of ``instrument`` as of date ``d``.

    Starts from the current row and undoes every applied ``StateChange`` for that
    instrument with ``effective_date > d``, latest first. Applied diffs use
    ``{"field": "amount_outstanding"|"status", "before": ..., "after": ...}``.
    """
    amount = Decimal(instrument.amount_outstanding)
    status = str(instrument.status)

    changes = session.scalars(
        select(StateChange)
        .where(
            StateChange.instrument_id == instrument.id,
            StateChange.status == "applied",
            StateChange.effective_date.is_not(None),
            StateChange.effective_date > d,
        )
        .order_by(StateChange.effective_date.desc(), StateChange.created_at.desc())
    ).all()

    for change in changes:
        diff = change.proposed_diff or {}
        field = diff.get("field")
        if field not in {"amount_outstanding", "status"}:
            continue
        if "before" not in diff:
            continue
        if field == "amount_outstanding":
            amount = Decimal(str(diff["before"]))
        else:
            status = str(diff["before"])

    return amount, status


def _instrument_row(inst: Instrument, *, amount: Decimal, status: str) -> dict[str, Any]:
    return {
        "id": inst.id,
        "key": inst.key,
        "name": inst.name,
        "issuer_id": inst.issuer_id,
        "maturity": inst.maturity,
        "amount_outstanding": amount,
        "currency": inst.currency,
        "instrument_type": inst.instrument_type,
        "cusip": inst.cusip,
        "status": status,
        "issue_date": inst.issue_date,
    }


def _sum_rows_usd(
    session: Session,
    rows: list[tuple[Instrument, Decimal, str]],
    *,
    as_of: date,
    context: str,
    rates_used: dict[str, dict],
) -> Decimal:
    total = Decimal("0")
    for inst, amount, _status in rows:
        try:
            total += amount_to_usd(
                session,
                amount,
                inst.currency or "USD",
                as_of,
                rates_used=rates_used,
            )
        except FxRateMissingError as exc:
            raise NonUsdAmountError(
                f"{context}: cannot convert {inst.key or inst.id} ({inst.currency}) to USD: {exc}"
            ) from exc
    return total


def _candidate_for_schedule(inst: Instrument, schedule_as_of: date) -> bool:
    """Issued on/before the schedule date (or unknown), maturing after it."""
    if inst.maturity <= schedule_as_of:
        return False
    return inst.issue_date is None or inst.issue_date <= schedule_as_of


def _instrument_sort_key(row: tuple[Instrument, Decimal, str]) -> tuple:
    inst, _amount, _status = row
    return (inst.maturity, inst.key or "", inst.id)


def maturity_wall(session: Session, issuer_id: str, as_of: date) -> dict[str, Any]:
    """Two-layer maturity wall for an issuer as of ``as_of`` (CONTEXT.md §8).

    Reconstructs instrument amounts/statuses via ``instrument_state_at`` so replay can
    evaluate any past date. Unattributed remainder and breach use state at
    ``schedule.as_of``; ``named_active`` / ``repaid_since_schedule`` use state at
    ``as_of``. ``new_since_schedule`` requires ``schedule.as_of < issue_date ≤ as_of``.
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
        "fx_rates_used": {},
    }
    if schedule is None:
        return result

    result["schedule_id"] = schedule.id
    result["schedule_as_of"] = schedule.as_of
    result["basis"] = schedule.basis
    result["source_url"] = schedule.source_url
    schedule_as_of = schedule.as_of
    rates_used: dict[str, dict] = {}

    issuer_ids = {issuer_id}
    if schedule.basis == "consolidated":
        issuer_ids = _subsidiary_ids(session, issuer_id)

    instruments = session.scalars(
        select(Instrument).where(Instrument.issuer_id.in_(issuer_ids))
    ).all()

    # Precompute states at schedule.as_of and at evaluation as_of.
    state_at_schedule: dict[str, tuple[Decimal, str]] = {}
    state_at_as_of: dict[str, tuple[Decimal, str]] = {}
    for inst in instruments:
        state_at_schedule[inst.id] = instrument_state_at(session, inst, schedule_as_of)
        state_at_as_of[inst.id] = instrument_state_at(session, inst, as_of)

    by_label_schedule: dict[str, list[tuple[Instrument, Decimal, str]]] = {}
    by_label_new: dict[str, list[tuple[Instrument, Decimal, str]]] = {}

    for inst in instruments:
        label = bucket_for(inst.maturity, schedule.buckets)
        if label is None:
            continue

        amt_s, status_s = state_at_schedule[inst.id]
        amt_a, status_a = state_at_as_of[inst.id]

        if _candidate_for_schedule(inst, schedule_as_of) and status_s == "active":
            by_label_schedule.setdefault(label, []).append((inst, amt_s, status_s))

        if (
            inst.issue_date is not None
            and inst.issue_date > schedule_as_of
            and inst.issue_date <= as_of
            and status_a == "active"
            and inst.maturity > as_of
        ):
            by_label_new.setdefault(label, []).append((inst, amt_a, status_a))

    buckets_out: dict[str, Any] = {}
    for bucket in schedule.buckets:
        label = str(bucket.get("label") or "")
        if not label:
            continue
        schedule_amount = Decimal(str(bucket.get("amount") or 0))
        schedule_named = by_label_schedule.get(label, [])
        new_named = by_label_new.get(label, [])

        named_active: list[tuple[Instrument, Decimal, str]] = []
        repaid: list[tuple[Instrument, Decimal, str]] = []
        for inst, _amt_s, _status_s in schedule_named:
            amt_a, status_a = state_at_as_of[inst.id]
            if status_a == "active" and inst.maturity > as_of:
                named_active.append((inst, amt_a, status_a))
            else:
                repaid.append((inst, amt_a, status_a))

        named_active = sorted(named_active, key=_instrument_sort_key)
        repaid = sorted(repaid, key=_instrument_sort_key)
        new_named = sorted(new_named, key=_instrument_sort_key)

        # Schedule backbone is already USD; convert named instrument notionals at as_of.
        schedule_sum = _sum_rows_usd(
            session,
            schedule_named,
            as_of=schedule_as_of,
            context=f"bucket {label} schedule set",
            rates_used=rates_used,
        )
        named_active_sum = _sum_rows_usd(
            session,
            named_active,
            as_of=as_of,
            context=f"bucket {label} named_active",
            rates_used=rates_used,
        )
        new_sum = _sum_rows_usd(
            session,
            new_named,
            as_of=as_of,
            context=f"bucket {label} new_since_schedule",
            rates_used=rates_used,
        )
        repaid_sum = _sum_rows_usd(
            session,
            repaid,
            as_of=as_of,
            context=f"bucket {label} repaid_since_schedule",
            rates_used=rates_used,
        )

        breach = schedule_sum > schedule_amount
        unattributed = max(Decimal("0"), schedule_amount - schedule_sum)
        total_outstanding = named_active_sum + new_sum + unattributed

        buckets_out[label] = {
            "label": label,
            "period_end": _period_end(bucket),
            "schedule_amount": schedule_amount,
            "named_active": [_instrument_row(i, amount=a, status=s) for i, a, s in named_active],
            "named_active_sum": named_active_sum,
            "repaid_since_schedule": [_instrument_row(i, amount=a, status=s) for i, a, s in repaid],
            "repaid_since_schedule_sum": repaid_sum,
            "new_since_schedule": [_instrument_row(i, amount=a, status=s) for i, a, s in new_named],
            "new_since_schedule_sum": new_sum,
            "unattributed": unattributed,
            "breach": breach,
            "total_outstanding": total_outstanding,
        }

    result["buckets"] = buckets_out
    result["fx_rates_used"] = rates_used
    return result
