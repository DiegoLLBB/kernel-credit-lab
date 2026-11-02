"""Reconcile Kernel accurate vs caught-but-wrong groups from stored outputs only."""

from __future__ import annotations

import json
from typing import Any

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
    _expected_alert_groups,
    _expected_amount,
    _material_changes,
    _maturity_year,
)
from app.models import Event


def _line(
    expected: dict[str, Any],
    diff: dict[str, Any] | None,
    matched: bool,
    detail: str,
) -> dict:
    mat = (diff or {}).get("maturity")
    act_year = int(str(mat)[:4]) if mat and str(mat)[:4].isdigit() else None
    act_amt = None
    if diff:
        for k in ("amount_outstanding", "after", "new_amount", "reduce_by"):
            if diff.get(k) is not None:
                act_amt = _parse_money(diff.get(k))
                break
        if expected.get("action") == "reduce_outstanding" and expected.get("by") is not None:
            before = _parse_money(diff.get("before"))
            after = _parse_money(diff.get("after"))
            if before is not None and after is not None:
                act_amt = before - after
    exp_amt = _expected_amount(expected)
    return {
        "action": expected.get("action"),
        "name": expected.get("name") or expected.get("instrument_key") or expected.get("key"),
        "expected_year": _maturity_year(expected),
        "expected_amount": float(exp_amt) if exp_amt is not None else None,
        "expected_currency": expected.get("currency") or "USD",
        "recorded_year": act_year,
        "recorded_amount": float(act_amt) if act_amt is not None else None,
        "recorded_currency": (diff or {}).get("currency"),
        "recorded_name": (diff or {}).get("name"),
        "matched": matched,
        "detail": detail,
    }


def _group_lines(session, group) -> tuple[list[dict], int, int]:
    lines: list[dict] = []
    applied_count = 0
    material = 0
    for exp in group:
        if not exp.expected_alert:
            continue
        event = session.get(Event, exp.event_id) if exp.event_id else None
        if event is None:
            continue
        applied = _applied_changes_for_event(session, event)
        applied_count = len(applied)
        claimed: set[int] = set()
        for change in _material_changes(exp):
            material += 1
            match = _match_change(
                session,
                exp.key,
                change,
                applied,
                as_of=event.event_date,
                claimed=claimed,
            )
            diff = ((match.actual or {}).get("proposed_diff") or {}) if match.actual else None
            lines.append(_line(change, diff, match.matched, match.detail))
    return lines, material, applied_count


def main() -> int:
    with SessionLocal() as session:
        report = evaluate_latest(session)
        per_event = [r.to_dict() for r in report.per_event]
        groups = _expected_alert_groups(session)

        accurate: list[dict] = []
        wrong: list[dict] = []
        not_caught: list[str] = []

        for score_key, group, _ in groups:
            keys = [g.key for g in group]
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
                            break
            if not caught:
                not_caught.append(",".join(keys))
                continue
            ok, detail = _agent_group_accurate(session, group)
            lines, material_n, applied_n = _group_lines(session, group)
            row = {
                "keys": keys,
                "score_key": score_key,
                "accurate": ok,
                "detail": detail,
                "material_expected_lines": material_n,
                "applied_changes": applied_n,
                "lines": lines,
            }
            if ok:
                accurate.append(row)
            else:
                wrong.append(row)

        # Prior wrong set from first comparison dig (for "moved" detection).
        prior_wrong = {
            "amzn_usd_mar",
            "amzn_eur_mar",
            "dec_term_loan",
            "duk_parent_tl_repaid",
            "amzn_usd_jul",
            "dep_term_loan",
        }
        moved = []
        for row in accurate:
            if any(k in prior_wrong for k in row["keys"]):
                moved.append(row)

        out = {
            "accurate_count": len(accurate),
            "caught_but_wrong_count": len(wrong),
            "not_caught_count": len(not_caught),
            "not_caught": not_caught,
            "accurate": accurate,
            "caught_but_wrong": wrong,
            "moved_from_prior_wrong": moved,
        }
        print(json.dumps(out, indent=2, default=str))

        # Also dump raw applied diffs for multi-tranche keys of interest.
        focus = {"amzn_usd_mar", "amzn_eur_mar", "amzn_usd_jul"}
        focus_out: dict[str, Any] = {}
        for _score_key, group, _ in groups:
            keys = [g.key for g in group]
            if not focus.intersection(keys):
                continue
            for exp in group:
                if exp.key not in focus:
                    continue
                event = session.get(Event, exp.event_id) if exp.event_id else None
                if event is None:
                    continue
                applied = _applied_changes_for_event(session, event)
                focus_out[exp.key] = [
                    {
                        "id": c.id,
                        "proposed_diff": c.proposed_diff,
                        "effective_date": (
                            c.effective_date.isoformat() if c.effective_date else None
                        ),
                    }
                    for c in applied
                ]
        print("\n=== APPLIED DIFFS (Amazon multi-tranche) ===")
        print(json.dumps(focus_out, indent=2, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
