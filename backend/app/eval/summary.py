"""Single source of truth for the replay score sentence shown on the overview."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.eval.replay_eval import AlertConfusion, evaluate_latest
from app.models import ReplayEvaluation

_MISS_STATUSES = frozenset(
    {"logged", "missing", "paused", "failed", "skipped", "needs_input", "refused"}
)


def _counts_from_row(row: ReplayEvaluation) -> AlertConfusion:
    raw = row.alert_counts if isinstance(row.alert_counts, dict) else None
    if raw:
        return AlertConfusion(
            true_positive=int(raw.get("true_positive") or 0),
            false_positive=int(raw.get("false_positive") or 0),
            true_negative=int(raw.get("true_negative") or 0),
            false_negative=int(raw.get("false_negative") or 0),
            incomplete_expected_alert=int(raw.get("incomplete_expected_alert") or 0),
            incomplete_expected_no_alert=int(raw.get("incomplete_expected_no_alert") or 0),
            failed_expected_alert=int(raw.get("failed_expected_alert") or 0),
            failed_expected_no_alert=int(raw.get("failed_expected_no_alert") or 0),
        )
    return AlertConfusion()


def _fn_covers_parked(counts: AlertConfusion) -> bool:
    """True when false_negative already includes incomplete+failed expected alerts."""
    parked = counts.incomplete_expected_alert + counts.failed_expected_alert
    if parked == 0:
        return True
    # New scoring bumps FN for each parked expected-alert, so FN >= parked
    # (plus any logged misses). Legacy rows keep FN as logged-only misses.
    return counts.false_negative >= parked


def _detail_events(per_event: list[Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    missed: list[dict[str, str]] = []
    false_alerts: list[dict[str, str]] = []
    for e in per_event or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("replay_key") or "event")
        day = str(e.get("event_date") or "")
        expected = e.get("expected_alert")
        got = str(e.get("got") or "").lower()
        # Prefer the scored ``miss`` flag so sibling log expectations of a merged
        # alerted event are not listed as false positives.
        if expected is True and (
            e.get("miss") is True or (e.get("miss") is None and got in _MISS_STATUSES)
        ):
            if got.startswith("alert"):
                continue
            missed.append({"name": name, "date": day})
        elif expected is False and got.startswith("alert") and e.get("miss") is True:
            false_alerts.append({"name": name, "date": day})
    return missed, false_alerts


def build_eval_summary(
    session: Session, row: ReplayEvaluation | None = None
) -> dict[str, Any] | None:
    """Caught X of Y, Z false; precision=X/(X+Z), recall=X/Y — one count.

    Prefers ``alert_counts`` on the latest ``ReplayEvaluation`` row. When the row
    still parks paused/failed expected alerts outside ``false_negative`` (legacy),
    recomputes via ``evaluate_latest`` so they count as misses.
    """
    if row is None:
        row = session.scalars(
            select(ReplayEvaluation).order_by(ReplayEvaluation.evaluated_at.desc())
        ).first()
    if row is None:
        return None

    counts = _counts_from_row(row)
    needs_recompute = (
        not row.alert_counts
        or (
            counts.true_positive
            + counts.false_positive
            + counts.false_negative
            + counts.true_negative
            == 0
        )
        or not _fn_covers_parked(counts)
    )
    if needs_recompute:
        report = evaluate_latest(session)
        counts = report.alert
        row.alert_counts = counts.to_dict()
        row.per_event = [r.to_dict() for r in report.per_event]
        if counts.precision is not None:
            row.precision = Decimal(str(counts.precision))
        if counts.recall is not None:
            row.recall = Decimal(str(counts.recall))
        session.add(row)

    x = counts.true_positive
    y = counts.true_positive + counts.false_negative
    z = counts.false_positive
    precision = (x / (x + z)) if (x + z) else None
    recall = (x / y) if y else None
    false_word = "false alert" if z == 1 else "false alerts"
    if y == 0 and z == 0:
        text = "No scored material changes yet."
    else:
        text = f"Caught {x} of {y} material changes, {z} {false_word}"
    missed, false_list = _detail_events(list(row.per_event or []))
    return {
        "text": text,
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
        "precision": precision
        if precision is not None
        else (float(row.precision) if row.precision is not None else None),
        "recall": recall
        if recall is not None
        else (float(row.recall) if row.recall is not None else None),
        "incomplete": bool(row.incomplete),
        "caught": x,
        "expected_material": y,
        "false_alerts": z,
        "missed_events": missed,
        "false_events": false_list,
    }
