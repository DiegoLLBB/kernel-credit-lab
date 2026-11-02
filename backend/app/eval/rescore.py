"""Rescore Kernel agent + plain-RAG baseline from stored outputs (no model calls).

Adds number-accuracy (right fiscal year and amount) and wall/record-keeping
metrics onto existing ``replay_evaluation`` rows. Does not touch the agent.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.eval.replay_eval import (
    AMOUNT_TOLERANCE,
    _amount_within_tol,
    _applied_changes_for_event,
    _eval_end_state_check,
    _face_amount_usd,
    _match_change,
    _parse_money,
    evaluate_latest,
    load_end_state_checks,
)
from app.eval.summary import build_eval_summary
from app.models import Event, ReplayEvaluation, ReplayExpectation
from app.tools.maturity import maturity_wall


def _maturity_year(change: dict[str, Any]) -> int | None:
    if change.get("maturity_year") is not None:
        try:
            return int(change["maturity_year"])
        except (TypeError, ValueError):
            return None
    mat = change.get("maturity")
    if mat is None:
        return None
    text = str(mat).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _expected_amount(change: dict[str, Any]) -> Decimal | None:
    for key in ("new_amount", "amount", "by", "amount_outstanding"):
        if change.get(key) is not None:
            return _parse_money(change[key])
    return None


def _material_changes(exp: ReplayExpectation) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for change in exp.expected_changes or []:
        if not isinstance(change, dict):
            continue
        if change.get("action") in {None, "log_only"}:
            continue
        out.append(change)
    return out


def _expected_alert_groups(
    session: Session,
) -> list[tuple[str, list[ReplayExpectation], bool]]:
    """Return (score_key, sibling expectations, group_expected_alert) for alert scoring."""
    expectations = session.scalars(
        select(ReplayExpectation).order_by(ReplayExpectation.available_from, ReplayExpectation.key)
    ).all()
    siblings: dict[str, list[ReplayExpectation]] = {}
    for exp in expectations:
        if exp.event_id:
            siblings.setdefault(exp.event_id, []).append(exp)

    seen: set[str] = set()
    groups: list[tuple[str, list[ReplayExpectation], bool]] = []
    for exp in expectations:
        if exp.event_id and exp.event_id in siblings:
            score_key = f"event:{exp.event_id}"
            group = siblings[exp.event_id]
            group_expected = any(bool(s.expected_alert) for s in group)
        else:
            score_key = f"key:{exp.key}"
            group = [exp]
            group_expected = bool(exp.expected_alert)
        if score_key in seen:
            continue
        seen.add(score_key)
        if not group_expected:
            continue
        groups.append((score_key, group, True))
    return groups


def _agent_group_caught(per_event: list[Any], group: list[ReplayExpectation]) -> bool:
    keys = {e.key for e in group}
    for row in per_event or []:
        if not isinstance(row, dict):
            continue
        if row.get("replay_key") not in keys:
            continue
        if row.get("expected_alert") is True and str(row.get("got") or "").startswith("alert"):
            return True
        # Merged group: any sibling with got=alerted and group expected counts.
        if (
            str(row.get("got") or "").startswith("alert")
            and any(bool(g.expected_alert) for g in group)
            and (row.get("expected_alert") is True or row.get("miss") is False)
        ):
            return True
    return False


def _agent_group_accurate(session: Session, group: list[ReplayExpectation]) -> tuple[bool, str]:
    """True when every material expected change matches year + amount (±tol, FX→USD)."""
    problems: list[str] = []
    any_material = False
    for exp in group:
        if not exp.expected_alert:
            continue
        event = session.get(Event, exp.event_id) if exp.event_id else None
        if event is None:
            problems.append(f"{exp.key}: missing event")
            continue
        applied = _applied_changes_for_event(session, event)
        claimed: set[int] = set()
        for change in _material_changes(exp):
            any_material = True
            match = _match_change(
                session,
                exp.key,
                change,
                applied,
                as_of=event.event_date,
                claimed=claimed,
            )
            if not match.matched:
                problems.append(f"{exp.key}: {match.detail}")
    if not any_material:
        # Expected alert with only log_only changes: accurate if caught (no numbers to check).
        return True, "no numeric changes required"
    return (not problems), "; ".join(problems) if problems else "ok"


def _baseline_answer_for_group(
    per_event: list[Any], group: list[ReplayExpectation]
) -> dict[str, Any] | None:
    by_key = {
        str(r.get("replay_key")): r
        for r in (per_event or [])
        if isinstance(r, dict) and r.get("replay_key")
    }
    # Prefer the expected_alert sibling's answer.
    for exp in group:
        if exp.expected_alert and exp.key in by_key:
            return by_key[exp.key]
    for exp in group:
        if exp.key in by_key:
            return by_key[exp.key]
    return None


def _quote_contains_year(quote: Any, year: int) -> bool:
    if quote is None:
        return False
    return re.search(rf"(?<!\d){year}(?!\d)", str(quote)) is not None


def _baseline_group_accurate(
    session: Session,
    group: list[ReplayExpectation],
    answer: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not answer or answer.get("material") is not True:
        return False, "not caught"
    years: set[int] = set()
    amounts: list[Decimal] = []
    as_of: date | None = None
    for exp in group:
        if not exp.expected_alert:
            continue
        event = session.get(Event, exp.event_id) if exp.event_id else None
        if event is not None and as_of is None:
            as_of = event.event_date
        for change in _material_changes(exp):
            y = _maturity_year(change)
            if y is not None:
                years.add(y)
            amt = _expected_amount(change)
            if amt is not None:
                # Face → USD at event-date FX for EUR/GBP/CAD (USD unchanged).
                amt_usd = _face_amount_usd(
                    session, amt, str(change.get("currency") or "USD"), as_of
                )
                # Signed: adds positive, reduce/repay negative when "by" is the reduction.
                if change.get("action") in {"reduce_outstanding", "mark_repaid", "mark_redeemed"}:
                    if change.get("by") is not None:
                        by_amt = _parse_money(change.get("by")) or amt
                        by_usd = _face_amount_usd(
                            session, by_amt, str(change.get("currency") or "USD"), as_of
                        )
                        amounts.append(-abs(by_usd))
                    elif change.get("action") == "reduce_outstanding" and change.get("new_amount"):
                        # Prefer 'by' when present; else skip net from new_amount alone.
                        pass
                    else:
                        amounts.append(-abs(amt_usd))
                else:
                    amounts.append(amt_usd)
    fy = answer.get("fiscal_year")
    year_ok = True
    if years:
        if fy is None:
            # Bug B: when fiscal_year is null, accept if the quote names an expected year.
            year_ok = any(_quote_contains_year(answer.get("quote"), y) for y in years)
        else:
            try:
                year_ok = int(fy) in years
            except (TypeError, ValueError):
                year_ok = False
    amount_ok = True
    raw_amount = answer.get("amount_change_usd")
    if amounts:
        expected_net = sum(amounts, Decimal("0"))
        if raw_amount is None:
            amount_ok = False
        else:
            actual = _parse_money(raw_amount)
            # Also accept absolute value match (model may omit sign).
            amount_ok = actual is not None and (
                _amount_within_tol(actual, expected_net)
                or _amount_within_tol(abs(actual), abs(expected_net))
            )
    elif raw_amount is not None:
        # Model invented an amount when expectation had none.
        amount_ok = False
    if year_ok and amount_ok:
        return True, "ok"
    bits = []
    if not year_ok:
        bits.append(f"year {fy!r} not in {sorted(years) or 'n/a'}")
    if not amount_ok:
        bits.append(f"amount {raw_amount!r} vs expected {float(sum(amounts)) if amounts else None}")
    return False, "; ".join(bits)


def _wall_bucket_score(session: Session) -> tuple[int, int, list[dict[str, Any]]]:
    """Compare maturity-wall year buckets to expected year totals after each alert event.

    For each expected-alert expectation with maturity-year amounts, check that the
    issuer's wall bucket for that year (as of the event date) is at least the
    expected add (within ±1%), or that repaid/reduced instruments are reflected.
    """
    passed = 0
    total = 0
    details: list[dict[str, Any]] = []
    expectations = session.scalars(
        select(ReplayExpectation).order_by(ReplayExpectation.available_from, ReplayExpectation.key)
    ).all()
    for exp in expectations:
        if not exp.expected_alert:
            continue
        event = session.get(Event, exp.event_id) if exp.event_id else None
        if event is None or event.issuer_id is None:
            continue
        as_of = event.event_date
        wall = maturity_wall(session, event.issuer_id, as_of)
        buckets = wall.get("buckets") or {}
        year_totals: dict[int, Decimal] = {}
        for label, bucket in buckets.items():
            if not isinstance(bucket, dict):
                continue
            # Labels like "2028" or "FY2028"
            digits = "".join(ch for ch in str(label) if ch.isdigit())
            if len(digits) >= 4:
                year = int(digits[:4])
                year_totals[year] = year_totals.get(year, Decimal("0")) + (
                    _parse_money(bucket.get("total_outstanding")) or Decimal("0")
                )
        for change in _material_changes(exp):
            if change.get("action") != "add_instrument":
                continue
            year = _maturity_year(change)
            amount = _expected_amount(change)
            if year is None or amount is None:
                continue
            amount_usd = _face_amount_usd(
                session, amount, str(change.get("currency") or "USD"), as_of
            )
            total += 1
            actual = year_totals.get(year, Decimal("0"))
            # Wall holds USD year totals; require bucket ≥ expected tranche (FX at event date).
            ok = actual >= amount_usd * (Decimal("1") - AMOUNT_TOLERANCE)
            if ok:
                passed += 1
            details.append(
                {
                    "key": exp.key,
                    "year": year,
                    "expected_amount": float(amount_usd),
                    "wall_total": float(actual),
                    "passed": ok,
                }
            )
    # Also fold classic end-state instrument checks into the wall/state score.
    for block in load_end_state_checks():
        as_of = date.fromisoformat(str(block["as_of"]))
        source = block.get("source")
        for check in block.get("checks") or []:
            total += 1
            result = _eval_end_state_check(session, as_of, source, str(check))
            if result.passed:
                passed += 1
            details.append(
                {
                    "key": f"end_state:{as_of}",
                    "check": check,
                    "passed": result.passed,
                    "detail": getattr(result, "detail", None),
                }
            )
    return passed, total, details


def rescore_agent(session: Session) -> dict[str, Any]:
    report = evaluate_latest(session)
    # Prefer latest agent evaluation row; refresh alert counts from report.
    row = session.scalars(
        select(ReplayEvaluation)
        .where(ReplayEvaluation.kind == "agent")
        .order_by(ReplayEvaluation.evaluated_at.desc())
    ).first()
    if row is None:
        row = session.scalars(
            select(ReplayEvaluation).order_by(ReplayEvaluation.evaluated_at.desc())
        ).first()
    if row is None:
        raise RuntimeError("no agent ReplayEvaluation row to rescore")

    per_event = [r.to_dict() for r in report.per_event]
    groups = _expected_alert_groups(session)
    accurate = 0
    group_details: list[dict[str, Any]] = []
    for score_key, group, _ in groups:
        caught = _agent_group_caught(per_event, group)
        # Recompute caught from report confusion via per_event miss flags.
        if not caught:
            # Fallback: true_positive groups have got=alerted and expected_alert
            for exp in group:
                if not exp.expected_alert:
                    continue
                for r in per_event:
                    if r.get("replay_key") == exp.key and str(r.get("got") or "").startswith(
                        "alert"
                    ):
                        caught = True
                        break
        ok, detail = (False, "not caught")
        if caught:
            ok, detail = _agent_group_accurate(session, group)
            if ok:
                accurate += 1
        group_details.append(
            {
                "score_key": score_key,
                "keys": [g.key for g in group],
                "caught": caught,
                "accurate": ok,
                "detail": detail,
            }
        )

    wall_passed, wall_total, wall_details = _wall_bucket_score(session)
    counts = report.alert.to_dict()
    counts.update(
        {
            "accurate_caught": accurate,
            "accurate_total": len(groups),
            "keeps_records": True,
            "wall_passed": wall_passed,
            "wall_total": wall_total,
        }
    )
    row.kind = "agent"
    row.alert_counts = counts
    row.per_event = per_event
    if report.alert.precision is not None:
        row.precision = Decimal(str(report.alert.precision))
    if report.alert.recall is not None:
        row.recall = Decimal(str(report.alert.recall))
    if row.unsupported_claims is None:
        row.unsupported_claims = 0
    session.add(row)
    session.flush()
    return {
        "kind": "agent",
        "accurate_caught": accurate,
        "accurate_total": len(groups),
        "wall_passed": wall_passed,
        "wall_total": wall_total,
        "groups": group_details,
        "wall_details": wall_details,
        "alert": counts,
    }


def rescore_baseline(session: Session) -> dict[str, Any]:
    row = session.scalars(
        select(ReplayEvaluation)
        .where(ReplayEvaluation.kind == "baseline")
        .order_by(ReplayEvaluation.evaluated_at.desc())
    ).first()
    if row is None:
        raise RuntimeError("no baseline ReplayEvaluation row to rescore")

    per_event = list(row.per_event or [])
    groups = _expected_alert_groups(session)
    accurate = 0
    group_details: list[dict[str, Any]] = []
    for score_key, group, _ in groups:
        answer = _baseline_answer_for_group(per_event, group)
        caught = bool(answer and answer.get("material") is True)
        ok, detail = _baseline_group_accurate(session, group, answer)
        if ok:
            accurate += 1
        group_details.append(
            {
                "score_key": score_key,
                "keys": [g.key for g in group],
                "caught": caught,
                "accurate": ok,
                "detail": detail,
                "fiscal_year": None if not answer else answer.get("fiscal_year"),
                "amount_change_usd": None if not answer else answer.get("amount_change_usd"),
            }
        )

    counts = dict(row.alert_counts or {})
    counts.update(
        {
            "accurate_caught": accurate,
            "accurate_total": len(groups),
            "keeps_records": False,
            "wall_passed": None,
            "wall_total": None,
        }
    )
    row.alert_counts = counts
    session.add(row)
    session.flush()
    return {
        "kind": "baseline",
        "accurate_caught": accurate,
        "accurate_total": len(groups),
        "keeps_records": False,
        "groups": group_details,
        "alert": counts,
    }


def rescore_all(session: Session) -> dict[str, Any]:
    agent = rescore_agent(session)
    baseline = rescore_baseline(session)
    session.commit()
    return {"agent": agent, "baseline": baseline}


def comparison_note(agent: dict[str, Any], baseline: dict[str, Any]) -> str:
    """Plain-English note from scored numbers only — no claims beyond them."""
    a_caught = agent.get("caught")
    a_total = agent.get("expected_material")
    a_acc = agent.get("accurate_caught")
    a_false = agent.get("false_alert_count")
    a_unsup = agent.get("unsupported_claims")
    a_cost = agent.get("cost_usd")
    a_wall_p = agent.get("wall_passed")
    a_wall_t = agent.get("wall_total")

    b_caught = baseline.get("caught")
    b_total = baseline.get("expected_material")
    b_acc = baseline.get("accurate_caught")
    b_false = baseline.get("false_alert_count")
    b_unsup = baseline.get("unsupported_claims")
    b_cost = baseline.get("cost_usd")

    parts = [
        f"Kernel caught {a_caught} of {a_total} material changes",
        f"with the right year and amount on {a_acc} of {a_total}",
        f"and {a_false} false alert{'s' if a_false != 1 else ''}",
        f"({a_unsup} unsupported claims).",
    ]
    if a_wall_p is not None and a_wall_t is not None:
        parts.append(
            f"It keeps maturity-wall records; {a_wall_p} of {a_wall_t} "
            "wall/end-state checks passed."
        )
    else:
        parts.append("It keeps maturity-wall records.")
    if a_cost is not None:
        parts.append(f"Replay cost was ${float(a_cost):.2f}.")

    parts.append(
        f"The one-shot model caught {b_caught} of {b_total}, "
        f"with the right year and amount on {b_acc} of {b_total}, "
        f"{b_false} false alert{'s' if b_false != 1 else ''}, "
        f"and {b_unsup} unsupported claims."
    )
    parts.append("It keeps no issuer records, so maturity-wall state is not scored (shown as —).")
    if b_cost is not None:
        parts.append(f"Baseline cost was ${float(b_cost):.2f}.")
    return " ".join(parts)


def build_comparison(session: Session) -> dict[str, Any]:
    """Overview table payload for Kernel vs one-shot model."""
    agent_sum = build_eval_summary(session, kind="agent") or {}
    base_sum = build_eval_summary(session, kind="baseline") or {}

    def _row(summary: dict[str, Any], *, keeps: str) -> dict[str, Any]:
        counts = {}
        kind = summary.get("kind") or "agent"
        row = session.scalars(
            select(ReplayEvaluation)
            .where(ReplayEvaluation.kind == kind)
            .order_by(ReplayEvaluation.evaluated_at.desc())
        ).first()
        if row and isinstance(row.alert_counts, dict):
            counts = row.alert_counts
        cost = None
        if row is not None and row.cost_usd is not None:
            cost = float(row.cost_usd)
        return {
            "label": summary.get("label") or kind,
            "kind": kind,
            "caught": summary.get("caught"),
            "expected_material": summary.get("expected_material"),
            "caught_text": summary.get("text"),
            "accurate_caught": counts.get("accurate_caught"),
            "accurate_total": counts.get("accurate_total") or summary.get("expected_material"),
            "false_alert_count": summary.get("false_alerts"),
            "unsupported_claims": summary.get("unsupported_claims"),
            "keeps_records": keeps,
            "wall_passed": counts.get("wall_passed"),
            "wall_total": counts.get("wall_total"),
            "cost_usd": cost,
        }

    agent_row = _row(agent_sum, keeps="yes")
    base_row = _row(base_sum, keeps="—")
    note = comparison_note(
        {
            **agent_row,
            "false_alert_count": agent_row["false_alert_count"],
        },
        {
            **base_row,
            "false_alert_count": base_row["false_alert_count"],
        },
    )
    return {"rows": [agent_row, base_row], "note": note}
