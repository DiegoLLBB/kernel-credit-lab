"""submit_alert with alert rules, materiality override, and claims check."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.audit import record_check, refusal_count
from app.agent.claims import ClaimsResult, claims_check
from app.agent.materiality import assess_materiality, is_missing_from_bond_list
from app.agent.normalize import (
    coerce_amount,
    detect_amount_scale,
    extract_numbers,
    parse_date_loose,
    parse_rate,
)
from app.agent.run import get_event
from app.agent.tools.base import ok, refuse
from app.models import (
    Alert,
    ChecklistItem,
    Fact,
    Instrument,
    MetricSnapshot,
    StateChange,
)
from app.models.trace import AgentRun

MAX_REFUSALS = 3


def _collect_metric_numbers(metrics_outputs: list[Any]) -> set[Decimal]:
    found: set[Decimal] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, Decimal):
            found.add(obj)
        else:
            try:
                if isinstance(obj, str) and obj.replace(".", "", 1).isdigit():
                    found.add(Decimal(obj))
                elif isinstance(obj, (int, float)):
                    found.add(Decimal(str(obj)))
            except Exception:
                return

    for block in metrics_outputs or []:
        walk(block)
    return found


def _quote_numbers_with_scale(facts: list[Fact]) -> set[Decimal]:
    """Numbers from fact quotes, plus table units × scale when an 'in millions' fact exists."""
    found: set[Decimal] = set()
    scales = [s for f in facts if (s := detect_amount_scale(f.exact_quote)) is not None]
    for fact in facts:
        nums = extract_numbers(fact.exact_quote)
        found.update(nums)
        if detect_amount_scale(fact.exact_quote) is not None:
            continue
        # Scale comma-grouped / money-like figures the way propose_change does.
        moneyish = extract_numbers(fact.exact_quote, money_only=True)
        for scale in scales:
            for n in moneyish:
                found.add(n * scale)
    return found


def _number_supported(
    value: Decimal, *, metric_nums: set[Decimal], quote_nums: set[Decimal]
) -> bool:
    if value in metric_nums or value in quote_nums:
        return True
    return any(abs(candidate - value) <= Decimal("1") for candidate in metric_nums | quote_nums)


def _missing_debt_flag(
    session: Session, run: AgentRun, event: Any, proposed: list[StateChange]
) -> bool:
    """True when an add_instrument change is for debt missing from the bond list."""
    from app.agent.tools.debt import available_from_for_event

    available = available_from_for_event(session, event)
    for change in proposed:
        diff = change.proposed_diff or {}
        if diff.get("action") != "add_instrument":
            continue
        issue_raw = diff.get("issue_date")
        if not issue_raw:
            continue
        issue = parse_date_loose(str(issue_raw))
        if issue is not None and is_missing_from_bond_list(
            issue_date=issue, available_from=available
        ):
            return True
    return False


async def submit_alert(
    session: Session,
    run: AgentRun,
    summary: str,
    findings: list[dict[str, Any]],
    material: bool,
    judgement_reason: str | None = None,
    *,
    claims_client: Any | None = None,
) -> dict[str, Any]:
    args = {
        "summary": summary,
        "findings": findings,
        "material": material,
        "judgement_reason": judgement_reason,
    }
    try:
        event = get_event(session, run)
    except ValueError as exc:
        return refuse(session, run, tool="submit_alert", arguments=args, reason=str(exc))

    problems: list[str] = []
    items = session.scalars(select(ChecklistItem).where(ChecklistItem.run_id == run.id)).all()
    facts = session.scalars(select(Fact).where(Fact.run_id == run.id)).all()
    facts_by_id = {f.id: f for f in facts}
    facts_by_item: dict[str, list[Fact]] = {}
    for fact in facts:
        facts_by_item.setdefault(fact.checklist_item_id, []).append(fact)

    for item in items:
        has_fact = bool(facts_by_item.get(item.id))
        escalated = item.status == "escalated"
        if not has_fact and not escalated:
            problems.append(f"checklist item {item.key!r} has no fact and is not escalated")

    for finding in findings or []:
        for cid in finding.get("citations") or finding.get("fact_ids") or []:
            if str(cid) not in facts_by_id:
                problems.append(f"cited fact {cid!r} does not exist in this run")

    quote_nums = _quote_numbers_with_scale(list(facts))
    metric_nums = _collect_metric_numbers(list(run.metrics_outputs or []))
    if not run.metrics_outputs:
        problems.append("compute_metrics has not been run")

    finding_texts = [str(f.get("statement") or f.get("text") or "") for f in findings or []]
    for text in [summary, *finding_texts]:
        for num in extract_numbers(text, money_only=True):
            if not _number_supported(num, metric_nums=metric_nums, quote_nums=quote_nums):
                problems.append(
                    f"number {num} in summary/findings appears in no compute_metrics "
                    "output or cited quote"
                )

    latest = (run.metrics_outputs or [])[-1] if run.metrics_outputs else None
    before_wall = ((latest or {}).get("before") or {}).get("issuer") or {}
    after_wall = ((latest or {}).get("after") or {}).get("issuer") or {}
    proposed = session.scalars(
        select(StateChange).where(StateChange.run_id == run.id, StateChange.status == "proposed")
    ).all()
    missing_debt = _missing_debt_flag(session, run, event, list(proposed))
    materiality = assess_materiality(
        before=before_wall,
        after=after_wall,
        new_debt_not_in_list=missing_debt,
    )
    code_material = bool(materiality["material"])
    reasons = list(materiality["reasons"])

    attempt = refusal_count(run, "submit_alert") + 1

    # ① Alert rules (code) — refuse before spending a claims-model call when rules fail.
    if problems:
        record_check(
            session,
            run,
            check_name="alert_rules",
            passed=False,
            problems=problems,
            attempt=attempt,
        )
        result = refuse(
            session,
            run,
            tool="submit_alert",
            arguments=args,
            reason="; ".join(problems),
            problems=problems,
        )
        if int(result.get("refusal_count") or 0) >= MAX_REFUSALS:
            run.status = "needs_input"
            session.flush()
            result = {**result, "status": "needs_input"}
        return result

    record_check(
        session,
        run,
        check_name="alert_rules",
        passed=True,
        problems={"materiality": materiality},
        attempt=attempt,
    )

    # ② Claims check (independent model) — await on this event loop; fail closed.
    claims_result: ClaimsResult = await claims_check(
        session,
        run,
        summary,
        findings or [],
        client=claims_client,
        facts_by_id=facts_by_id,
    )
    claims_passed = claims_result.status == "pass"
    record_check(
        session,
        run,
        check_name="claims",
        passed=claims_passed,
        problems=claims_result.to_audit(),
        attempt=attempt,
    )

    claims_problems: list[str] = []
    if claims_result.status == "error":
        claims_problems.append("claims check unavailable")
    elif claims_result.status == "fail":
        claims_problems.extend(claims_result.problems)

    # Code materiality cannot be downgraded. Judgement may upgrade when claims pass.
    final_material = code_material
    judgement_applied = False
    if code_material:
        final_material = True
    elif material:
        if not judgement_reason or not str(judgement_reason).strip():
            claims_problems.append(
                "judgement upgrade requires judgement_reason when material=true "
                "but code rules say no"
            )
        elif not claims_passed:
            claims_problems.append(
                f"claims check must pass for a judgement upgrade (got {claims_result.status!r})"
            )
        else:
            final_material = True
            judgement_applied = True
            reasons = [f"judgement (agent): {judgement_reason.strip()}"]

    if claims_problems:
        record_check(
            session,
            run,
            check_name="alert_rules",
            passed=False,
            problems=claims_problems,
            attempt=attempt,
        )
        result = refuse(
            session,
            run,
            tool="submit_alert",
            arguments=args,
            reason="; ".join(claims_problems),
            problems=claims_problems,
        )
        if int(result.get("refusal_count") or 0) >= MAX_REFUSALS:
            run.status = "needs_input"
            session.flush()
            result = {**result, "status": "needs_input"}
        return result

    for change in proposed:
        diff = change.proposed_diff or {}
        action = diff.get("action")
        if action in {"mark_redeemed", "mark_repaid", "mark_matured"} and change.instrument_id:
            inst = session.get(Instrument, change.instrument_id)
            if inst is not None:
                inst.status = str(diff.get("after") or "redeemed")
        elif action in {"reduce_outstanding", "update_amount"} and change.instrument_id:
            inst = session.get(Instrument, change.instrument_id)
            if inst is not None and diff.get("after") is not None:
                after = coerce_amount(diff["after"])
                if after is None:
                    raise ValueError(f"invalid amount in proposed change: {diff.get('after')!r}")
                inst.amount_outstanding = after
        elif action == "add_instrument":
            issuer_id = diff.get("issuer_id") or event.issuer_id
            coupon = parse_rate(diff.get("coupon") or "0")
            if coupon is None:
                raise ValueError(f"invalid coupon in proposed change: {diff.get('coupon')!r}")
            amount = coerce_amount(diff.get("amount_outstanding") or "0")
            if amount is None:
                raise ValueError(
                    f"invalid amount_outstanding in proposed change: "
                    f"{diff.get('amount_outstanding')!r}"
                )
            session.add(
                Instrument(
                    key=diff.get("instrument_key"),
                    issuer_id=str(issuer_id),
                    name=str(diff.get("name") or "New instrument"),
                    coupon=coupon,
                    maturity=date.fromisoformat(str(diff["maturity"])),
                    maturity_precision=str(diff.get("maturity_precision") or "day"),
                    issue_date=(
                        date.fromisoformat(str(diff["issue_date"]))
                        if diff.get("issue_date")
                        else None
                    ),
                    amount_outstanding=amount,
                    currency=str(diff.get("currency") or "USD"),
                    instrument_type=str(diff.get("instrument_type") or "bond"),
                    rate_type=str(diff.get("rate_type") or "fixed"),
                    status="active",
                )
            )
        change.status = "applied"
        if change.effective_date is None:
            change.effective_date = event.event_date

    snapshot = None
    if event.issuer_id and latest is not None:
        snapshot = MetricSnapshot(
            issuer_id=event.issuer_id,
            as_of=event.event_date,
            metrics={
                "before": latest.get("before"),
                "after": latest.get("after"),
                "metrics_id": latest.get("id"),
            },
            run_id=run.id,
        )
        session.add(snapshot)

    claims_audit = claims_result.to_audit()
    alert = None
    if final_material and event.issuer_id:
        alert = Alert(
            event_id=event.id,
            issuer_id=event.issuer_id,
            run_id=run.id,
            title=(summary[:200] if summary else "Material credit event"),
            summary=summary,
            findings=findings,
            before_metrics=(latest or {}).get("before"),
            after_metrics=(latest or {}).get("after"),
            materiality_reasons=reasons,
            claims_check=claims_audit,
            status="new",
        )
        session.add(alert)
        run.status = "alerted"
        event.status = "processed"
    else:
        run.status = "logged"
        event.status = "processed"
        run.result = {
            **(run.result or {}),
            "logged": True,
            "summary": summary,
            "findings": findings,
            "materiality": {**materiality, "reasons": reasons},
            "agent_material_flag": material,
            "judgement_reason": judgement_reason,
            "claims_check": claims_audit,
        }

    run.completed_at = datetime.now(UTC)
    session.flush()

    return ok(
        session,
        run,
        tool="submit_alert",
        arguments=args,
        result={
            "status": run.status,
            "material": final_material,
            "code_material": code_material,
            "materiality_reasons": reasons,
            "agent_material_flag": material,
            "judgement_applied": judgement_applied,
            "alert_id": alert.id if alert else None,
            "metric_snapshot_id": snapshot.id if snapshot else None,
            "claims_check": claims_result.status,
            "claims_check_detail": claims_audit,
            "applied_changes": [c.id for c in proposed],
        },
    )
