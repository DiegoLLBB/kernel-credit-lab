"""Print evidence for caught-but-wrong Kernel cases and failed baseline answers.

Stored outputs only — no model calls.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.eval.replay_eval import (
    _applied_changes_for_event,
    _match_change,
    _parse_money,
    evaluate_latest,
)
from app.eval.rescore import (
    _agent_group_accurate,
    _agent_group_caught,
    _baseline_answer_for_group,
    _baseline_group_accurate,
    _expected_alert_groups,
    _expected_amount,
    _material_changes,
    _maturity_year,
)
from app.models import Event, ReplayEvaluation


def _classify_kernel(detail: str, expected: dict[str, Any], actual_diff: dict[str, Any]) -> str:
    d = (detail or "").lower()
    if "no applied change" in d:
        return "wrong amount"  # missed the instrument entirely / not recorded
    if "maturity year" in d or "year " in d and "not in" in d:
        return "wrong year"
    if "amount" in d and "outside" in d:
        # FX heuristic: expected USD-like, actual far off by FX ratio
        exp_amt = _expected_amount(expected)
        act = None
        for k in ("amount_outstanding", "after", "new_amount"):
            if actual_diff.get(k) is not None:
                act = _parse_money(actual_diff.get(k))
                break
        if exp_amt and act and exp_amt != 0:
            ratio = float(act / exp_amt)
            if 0.7 < ratio < 1.4 and abs(ratio - 1.0) > 0.02:
                curr = (expected.get("currency") or actual_diff.get("currency") or "").upper()
                if curr and curr != "USD":
                    return "FX conversion"
            if abs(float(act) - float(exp_amt)) / float(abs(exp_amt)) <= 0.02:
                return "rounding"
        return "wrong amount"
    if "date mismatch" in d:
        return "wrong year"
    if "unparseable" in d or "missing" in d:
        return "evaluator bug"
    return "wrong amount"


def _classify_baseline(detail: str, answer: dict[str, Any], expected_net: Decimal | None) -> str:
    d = (detail or "").lower()
    if "not caught" in d:
        return "missing field"
    if "year" in d:
        return "wrong year"
    raw = answer.get("amount_change_usd")
    if raw is None and "amount" in d:
        return "missing field"
    if expected_net is not None and raw is not None:
        try:
            actual = Decimal(str(raw))
        except Exception:
            return "wrong amount"
        # millions vs dollars: actual ~ expected/1e6
        if expected_net != 0:
            if (
                abs(float(actual) - float(expected_net) / 1_000_000)
                / max(abs(float(expected_net) / 1_000_000), 1)
                < 0.05
            ):
                return "unit mismatch (millions vs dollars)"
            if (
                abs(float(actual) - float(expected_net) / 1_000)
                / max(abs(float(expected_net) / 1_000), 1)
                < 0.05
            ):
                return "unit mismatch (thousands vs dollars)"
        return "wrong amount"
    if "amount" in d:
        return "wrong amount"
    return "wrong amount"


def main() -> int:
    with SessionLocal() as session:
        report = evaluate_latest(session)
        per_event = [r.to_dict() for r in report.per_event]
        groups = _expected_alert_groups(session)

        print("=== KERNEL: caught but wrong year/amount ===")
        wrong: list[dict[str, Any]] = []
        for _score_key, group, _ in groups:
            caught = _agent_group_caught(per_event, group)
            if not caught:
                for exp in group:
                    if not exp.expected_alert:
                        continue
                    for r in per_event:
                        if r.get("replay_key") == exp.key and str(r.get("got") or "").startswith(
                            "alert"
                        ):
                            caught = True
            if not caught:
                continue
            ok, detail = _agent_group_accurate(session, group)
            if ok:
                continue
            # Expand per expected change
            for exp in group:
                if not exp.expected_alert:
                    continue
                event = session.get(Event, exp.event_id) if exp.event_id else None
                applied = _applied_changes_for_event(session, event) if event else []
                for change in _material_changes(exp):
                    match = _match_change(
                        session,
                        exp.key,
                        change,
                        applied,
                        as_of=event.event_date if event else None,
                    )
                    diff = ((match.actual or {}).get("proposed_diff") or {}) if match.actual else {}
                    exp_year = _maturity_year(change)
                    exp_amt = _expected_amount(change)
                    act_year = None
                    mat = diff.get("maturity")
                    if mat and str(mat)[:4].isdigit():
                        act_year = int(str(mat)[:4])
                    act_amt = None
                    for k in ("amount_outstanding", "after", "new_amount", "reduce_by"):
                        if diff.get(k) is not None:
                            act_amt = _parse_money(diff.get(k))
                            break
                    if (
                        change.get("action") == "reduce_outstanding"
                        and change.get("by") is not None
                    ):
                        before = _parse_money(diff.get("before"))
                        after = _parse_money(diff.get("after"))
                        if before is not None and after is not None:
                            act_amt = before - after
                    cause = _classify_kernel(
                        match.detail if not match.matched else detail, change, diff
                    )
                    if match.matched:
                        # Still in wrong group — find which sub-check failed
                        if exp_year is not None and mat and not str(mat).startswith(str(exp_year)):
                            cause = "wrong year"
                        elif exp_amt is not None and act_amt is not None:
                            cause = _classify_kernel(
                                f"amount {act_amt} outside 1% of expected {exp_amt}",
                                change,
                                diff,
                            )
                        else:
                            cause = "wrong amount"
                    wrong.append(
                        {
                            "event_key": exp.key,
                            "action": change.get("action"),
                            "expected_year": exp_year,
                            "expected_amount": float(exp_amt) if exp_amt is not None else None,
                            "currency": change.get("currency"),
                            "kernel_year": act_year,
                            "kernel_amount": float(act_amt) if act_amt is not None else None,
                            "matched": match.matched,
                            "match_detail": match.detail,
                            "group_detail": detail,
                            "cause": cause,
                            "proposed_diff": diff,
                        }
                    )

        # Deduplicate to one row per event_key focusing on failing changes only
        failing = [w for w in wrong if not w["matched"] or w["cause"] != "ok"]
        # Prefer unique event keys for the "5 cases"
        by_key: dict[str, list[dict[str, Any]]] = {}
        for w in failing:
            by_key.setdefault(w["event_key"], []).append(w)
        print(f"caught_but_wrong_groups: {len(by_key)}")
        for key, items in by_key.items():
            print(json.dumps({"event_key": key, "changes": items}, indent=2, default=str))

        print("\n=== BASELINE: sample caught / attempted answers ===")
        base = session.scalars(
            select(ReplayEvaluation)
            .where(ReplayEvaluation.kind == "baseline")
            .order_by(ReplayEvaluation.evaluated_at.desc())
        ).first()
        if base is None:
            print("no baseline row")
            return 1
        b_per = list(base.per_event or [])
        shown = 0
        for _score_key, group, _ in groups:
            answer = _baseline_answer_for_group(b_per, group)
            ok, detail = _baseline_group_accurate(session, group, answer)
            # Prefer material=true failures, else any with an answer
            if answer is None:
                continue
            if ok:
                continue
            years: set[int] = set()
            amounts: list[Decimal] = []
            for exp in group:
                if not exp.expected_alert:
                    continue
                for change in _material_changes(exp):
                    y = _maturity_year(change)
                    if y is not None:
                        years.add(y)
                    amt = _expected_amount(change)
                    if amt is not None:
                        if change.get("action") in {
                            "reduce_outstanding",
                            "mark_repaid",
                            "mark_redeemed",
                        }:
                            if change.get("by") is not None:
                                amounts.append(-abs(_parse_money(change.get("by")) or amt))
                            else:
                                amounts.append(-abs(amt))
                        else:
                            amounts.append(amt)
            expected_net = sum(amounts, Decimal("0")) if amounts else None
            cause = _classify_baseline(detail, answer, expected_net)
            raw = {
                "material": answer.get("material"),
                "fiscal_year": answer.get("fiscal_year"),
                "amount_change_usd": answer.get("amount_change_usd"),
                "quote": answer.get("quote"),
            }
            print(
                json.dumps(
                    {
                        "group_keys": [g.key for g in group],
                        "raw_json": raw,
                        "scorer_parsed": {
                            "caught": answer.get("material") is True,
                            "fiscal_year": answer.get("fiscal_year"),
                            "amount_change_usd": answer.get("amount_change_usd"),
                            "expected_years": sorted(years),
                            "expected_net_usd": float(expected_net)
                            if expected_net is not None
                            else None,
                            "detail": detail,
                        },
                        "cause": cause,
                    },
                    indent=2,
                    default=str,
                )
            )
            shown += 1
            if shown >= 5:
                break

        print("\n=== ACCURATE KERNEL (for context) ===")
        for _score_key, group, _ in groups:
            caught = _agent_group_caught(per_event, group)
            if not caught:
                continue
            ok, detail = _agent_group_accurate(session, group)
            if ok:
                print([g.key for g in group], "OK", detail)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
