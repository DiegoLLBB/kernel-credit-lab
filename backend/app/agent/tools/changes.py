"""propose_change with evidence and consistency checks."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.audit import record_check, refusal_count
from app.agent.normalize import (
    amount_in_quote,
    date_in_quote,
    instrument_identified_in_quote,
    parse_amount,
    parse_date_loose,
)
from app.agent.run import get_event
from app.agent.tools.base import ok, refuse
from app.models import Fact, Instrument, StateChange
from app.models.trace import AgentRun

CHANGE_ACTIONS = frozenset(
    {
        "mark_redeemed",
        "mark_repaid",
        "mark_matured",
        "reduce_outstanding",
        "add_instrument",
        "update_amount",
    }
)


def _facts(session: Session, run: AgentRun) -> dict[str, Fact]:
    return {f.id: f for f in session.scalars(select(Fact).where(Fact.run_id == run.id)).all()}


def _resolve_instrument(session: Session, change: dict[str, Any]) -> Instrument | None:
    if change.get("instrument_id"):
        return session.get(Instrument, str(change["instrument_id"]))
    key = change.get("instrument_key")
    if key:
        return session.scalars(select(Instrument).where(Instrument.key == str(key))).first()
    return None


def _require_fact(
    facts: dict[str, Fact], fact_id: Any, label: str
) -> tuple[Fact | None, str | None]:
    if not fact_id or str(fact_id) not in facts:
        return None, f"missing or unknown fact citation for {label}"
    return facts[str(fact_id)], None


def _amount_in_quote(value: Any, quote: str) -> bool:
    return amount_in_quote(value, quote)


def _date_in_quote(value: Any, quote: str) -> bool:
    return date_in_quote(value, quote)


def _instrument_in_quote(inst: Instrument, quote: str) -> bool:
    return instrument_identified_in_quote(
        quote=quote,
        name=inst.name,
        cusip=inst.cusip,
        coupon=inst.coupon,
        maturity_year=inst.maturity.year if inst.maturity else None,
    )


def propose_change(session: Session, run: AgentRun, change: dict[str, Any]) -> dict[str, Any]:
    args = {"change": change}
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="propose_change", arguments=args, reason=str(exc))

    if not isinstance(change, dict):
        return refuse(
            session, run, tool="propose_change", arguments=args, reason="change must be an object"
        )

    action = str(change.get("action") or "")
    if action not in CHANGE_ACTIONS:
        return refuse(
            session,
            run,
            tool="propose_change",
            arguments=args,
            reason=f"unknown action {action!r}",
        )

    effective = parse_date_loose(str(change.get("effective_date") or ""))
    if effective is None:
        return refuse(
            session,
            run,
            tool="propose_change",
            arguments=args,
            reason="effective_date is required",
        )

    evidence = change.get("evidence") or {}
    if not isinstance(evidence, dict):
        return refuse(
            session,
            run,
            tool="propose_change",
            arguments=args,
            reason="evidence must be a map of field → fact_id",
        )

    facts = _facts(session, run)
    problems: list[str] = []
    proposed_diff: dict[str, Any]
    instrument_id: str | None

    if action in {"mark_redeemed", "mark_repaid", "mark_matured"}:
        inst = _resolve_instrument(session, change)
        if inst is None:
            problems.append("instrument does not exist")
            instrument_id = None
        else:
            instrument_id = inst.id

        status_after = {
            "mark_redeemed": "redeemed",
            "mark_repaid": "repaid",
            "mark_matured": "matured",
        }[action]

        inst_fact, err = _require_fact(facts, evidence.get("instrument"), "instrument")
        if err:
            problems.append(err)
        elif (
            inst is not None
            and inst_fact is not None
            and not _instrument_in_quote(inst, inst_fact.exact_quote)
        ):
            problems.append(
                "instrument not identified in cited quote "
                "(need CUSIP, full name, or coupon% and maturity year)"
            )

        date_fid = evidence.get("effective_date") or evidence.get("redemption_date")
        date_fact, err = _require_fact(facts, date_fid, "effective_date")
        if err:
            problems.append(err)
        elif date_fact is not None and not _date_in_quote(effective, date_fact.exact_quote):
            problems.append(f"date {effective.isoformat()!r} not found in cited quote")

        proposed_diff = {
            "action": action,
            "field": "status",
            "before": inst.status if inst else None,
            "after": status_after,
            "instrument_key": inst.key if inst else change.get("instrument_key"),
        }

    elif action in {"reduce_outstanding", "update_amount"}:
        inst = _resolve_instrument(session, change)
        if inst is None:
            problems.append("instrument does not exist")
            instrument_id = None
            proposed_diff = {"action": action}
        else:
            instrument_id = inst.id
            inst_fact, err = _require_fact(facts, evidence.get("instrument"), "instrument")
            if err:
                problems.append(err)
            elif inst_fact is not None and not _instrument_in_quote(inst, inst_fact.exact_quote):
                problems.append(
                    "instrument not identified in cited quote "
                    "(need CUSIP, full name, or coupon% and maturity year)"
                )

            if action == "reduce_outstanding":
                raw = change.get("reduce_by") or change.get("amount")
                try:
                    reduce_dec = (
                        raw
                        if isinstance(raw, Decimal)
                        else parse_amount(str(raw)) or Decimal(str(raw))
                    )
                except (InvalidOperation, TypeError):
                    reduce_dec = None
                    problems.append("reduce_by is not a number")
                amt_fid = evidence.get("reduce_by") or evidence.get("amount")
                amt_fact, err = _require_fact(facts, amt_fid, "amount")
                if err:
                    problems.append(err)
                elif (
                    reduce_dec is not None
                    and amt_fact is not None
                    and not _amount_in_quote(reduce_dec, amt_fact.exact_quote)
                ):
                    problems.append(f"amount {reduce_dec!r} not found in cited quote")
                if reduce_dec is not None and reduce_dec > Decimal(inst.amount_outstanding):
                    problems.append("reduction exceeds outstanding amount")
                new_amount = (
                    Decimal(inst.amount_outstanding) - reduce_dec
                    if reduce_dec is not None
                    else None
                )
            else:
                raw = change.get("new_amount") or change.get("amount")
                try:
                    new_amount = (
                        raw
                        if isinstance(raw, Decimal)
                        else parse_amount(str(raw)) or Decimal(str(raw))
                    )
                except (InvalidOperation, TypeError):
                    new_amount = None
                    problems.append("new_amount is not a number")
                amt_fid = evidence.get("new_amount") or evidence.get("amount")
                amt_fact, err = _require_fact(facts, amt_fid, "amount")
                if err:
                    problems.append(err)
                elif (
                    new_amount is not None
                    and amt_fact is not None
                    and not _amount_in_quote(new_amount, amt_fact.exact_quote)
                ):
                    problems.append(f"amount {new_amount!r} not found in cited quote")

            proposed_diff = {
                "action": action,
                "field": "amount_outstanding",
                "before": str(inst.amount_outstanding),
                "after": str(new_amount) if new_amount is not None else None,
                "instrument_key": inst.key,
            }

    else:  # add_instrument
        instrument_id = None
        key = change.get("instrument_key") or change.get("key")
        if key and session.scalars(select(Instrument).where(Instrument.key == str(key))).first():
            problems.append("instrument already exists")

        name = change.get("name")
        coupon = change.get("coupon")
        maturity_raw = change.get("maturity")
        issue_raw = change.get("issue_date")
        amount_raw = change.get("amount_outstanding") or change.get("amount")
        currency = change.get("currency") or "USD"
        instrument_type = change.get("instrument_type") or "bond"

        for field, value in [
            ("name", name),
            ("coupon", coupon),
            ("maturity", maturity_raw),
            ("amount_outstanding", amount_raw),
            ("currency", currency),
        ]:
            if value is None or value == "":
                problems.append(f"missing field {field}")

        maturity = parse_date_loose(str(maturity_raw)) if maturity_raw else None
        issue = parse_date_loose(str(issue_raw)) if issue_raw else None
        if maturity is None or (issue is not None and not (maturity > issue)):
            problems.append("maturity must come after issue_date")
        if not currency:
            problems.append("currency must be set")

        if amount_raw is not None:
            amt_fact, err = _require_fact(
                facts, evidence.get("amount") or evidence.get("amount_outstanding"), "amount"
            )
            if err:
                problems.append(err)
            elif amt_fact is not None and not _amount_in_quote(amount_raw, amt_fact.exact_quote):
                problems.append(f"amount {amount_raw!r} not found in cited quote")

        if maturity is not None:
            mat_fact, err = _require_fact(facts, evidence.get("maturity"), "maturity")
            if err:
                problems.append(err)
            elif mat_fact is not None and not _date_in_quote(maturity, mat_fact.exact_quote):
                problems.append(f"date {maturity.isoformat()!r} not found in cited quote")

        proposed_diff = {
            "action": "add_instrument",
            "instrument_key": key,
            "name": name,
            "coupon": str(coupon) if coupon is not None else None,
            "maturity": maturity.isoformat() if maturity else None,
            "issue_date": issue.isoformat() if issue else None,
            "amount_outstanding": str(amount_raw) if amount_raw is not None else None,
            "currency": currency,
            "instrument_type": instrument_type,
            "issuer_id": change.get("issuer_id") or event.issuer_id,
            "rate_type": change.get("rate_type") or "fixed",
        }

    attempt = refusal_count(run, "propose_change") + 1
    if problems:
        record_check(
            session,
            run,
            check_name="evidence",
            passed=False,
            problems=problems,
            attempt=attempt,
        )
        return refuse(
            session,
            run,
            tool="propose_change",
            arguments=args,
            reason="; ".join(problems),
            problems=problems,
        )

    record_check(session, run, check_name="evidence", passed=True, problems=None, attempt=attempt)
    row = StateChange(
        instrument_id=instrument_id,
        event_id=event.id,
        run_id=run.id,
        proposed_diff=proposed_diff,
        evidence={"citations": evidence, "change": change},
        status="proposed",
        effective_date=effective,
    )
    session.add(row)
    session.flush()
    return ok(
        session,
        run,
        tool="propose_change",
        arguments=args,
        result={"state_change_id": row.id, "status": "proposed"},
    )
