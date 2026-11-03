"""Map replay evaluation rows to human-readable alert verdicts for the dashboard."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.eval.rescore import _agent_group_accurate
from app.models import Alert, Event, ReplayEvaluation, ReplayExpectation

ReplayVerdict = Literal[
    "correct",
    "caught, numbers wrong",
    "false alert",
    "missed",
    "not yet scored",
]

_VERDICT_REASONS: dict[str, str] = {
    "correct": "Replay expected a material alert; Kernel raised one with matching numbers.",
    "caught, numbers wrong": (
        "Kernel alerted but amounts or fiscal years did not match expectations."
    ),
    "false alert": "Kernel alerted when the benchmark expected no material alert.",
    "missed": "Benchmark expected a material alert; Kernel did not raise one.",
    "not yet scored": "Live document or not yet in the latest replay evaluation.",
}

# Nested under ReplayEvaluation.alert_counts so overview can O(1) look up badges
# without re-running expectation / accuracy queries per feed item.
FEED_VERDICTS_KEY = "feed_verdicts"


def _latest_agent_eval(session: Session) -> ReplayEvaluation | None:
    return session.scalars(
        select(ReplayEvaluation)
        .where(ReplayEvaluation.kind == "agent")
        .order_by(ReplayEvaluation.evaluated_at.desc())
    ).first()


def _per_event_by_key_from_row(row: ReplayEvaluation | None) -> dict[str, dict[str, Any]]:
    if row is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in row.per_event or []:
        if isinstance(item, dict) and item.get("replay_key"):
            out[str(item["replay_key"])] = item
    return out


def _per_event_by_key(session: Session) -> dict[str, dict[str, Any]]:
    return _per_event_by_key_from_row(_latest_agent_eval(session))


def _expectations_for_event(session: Session, event: Event) -> list[ReplayExpectation]:
    """Expectations linked to this event (covers merged ``filing:…`` replay keys)."""
    linked = list(
        session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.event_id == event.id)
        ).all()
    )
    if linked:
        return linked
    if event.replay_key:
        exp = session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.key == event.replay_key)
        ).first()
        if exp is not None:
            if exp.event_id:
                return list(
                    session.scalars(
                        select(ReplayExpectation).where(ReplayExpectation.event_id == exp.event_id)
                    ).all()
                )
            return [exp]
    return []


def _per_event_rows_for_event(
    session: Session, event: Event
) -> tuple[list[dict[str, Any]], list[ReplayExpectation]]:
    """Match eval rows to an event via replay_key and/or linked expectation keys."""
    by_key = _per_event_by_key(session)
    if not by_key:
        return [], []
    group = _expectations_for_event(session, event)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    keys: list[str] = []
    if event.replay_key:
        keys.append(str(event.replay_key))
    keys.extend(exp.key for exp in group if exp.key)
    for key in keys:
        pe = by_key.get(key)
        if pe is not None and key not in seen:
            rows.append(pe)
            seen.add(key)
    return rows, group


def _got_alerted(got: Any) -> bool:
    return str(got or "").startswith("alert")


def _verdict_from_rows(
    session: Session,
    *,
    event: Event,
    rows: list[dict[str, Any]],
    group: list[ReplayExpectation],
    accuracy_cache: dict[str, bool],
) -> tuple[str | None, str | None]:
    if event.source == "live":
        return "not yet scored", _VERDICT_REASONS["not yet scored"]
    if not rows:
        return "not yet scored", _VERDICT_REASONS["not yet scored"]

    expected = any(r.get("expected_alert") is True for r in rows) or any(
        bool(exp.expected_alert) for exp in group
    )
    got = any(_got_alerted(r.get("got")) for r in rows)

    if expected and got:
        cache_key = event.id
        if cache_key not in accuracy_cache:
            if group:
                accuracy_cache[cache_key] = _agent_group_accurate(session, group)[0]
            else:
                accuracy_cache[cache_key] = True
        if accuracy_cache[cache_key]:
            return "correct", _VERDICT_REASONS["correct"]
        return "caught, numbers wrong", _VERDICT_REASONS["caught, numbers wrong"]
    if not expected and got:
        return "false alert", _VERDICT_REASONS["false alert"]
    if expected and not got:
        return "missed", _VERDICT_REASONS["missed"]
    return None, None


def replay_verdict_for_event(
    session: Session, event: Event | None
) -> tuple[str | None, str | None]:
    """Return (verdict label, reason) for dashboard badges."""
    if event is None:
        return None, None
    if event.source == "live":
        return "not yet scored", _VERDICT_REASONS["not yet scored"]
    rows, group = _per_event_rows_for_event(session, event)
    return _verdict_from_rows(session, event=event, rows=rows, group=group, accuracy_cache={})


def replay_verdict_for_alert(session: Session, alert: Alert) -> tuple[str | None, str | None]:
    event = session.get(Event, alert.event_id) if alert.event_id else None
    return replay_verdict_for_event(session, event)


def _expectation_indexes(
    session: Session,
) -> tuple[dict[str, list[ReplayExpectation]], dict[str, ReplayExpectation]]:
    expectations = list(session.scalars(select(ReplayExpectation)).all())
    by_event_id: dict[str, list[ReplayExpectation]] = {}
    by_key: dict[str, ReplayExpectation] = {}
    for exp in expectations:
        if exp.key:
            by_key[str(exp.key)] = exp
        if exp.event_id:
            by_event_id.setdefault(exp.event_id, []).append(exp)
    return by_event_id, by_key


def _group_for_event(
    event: Event,
    *,
    by_event_id: dict[str, list[ReplayExpectation]],
    by_exp_key: dict[str, ReplayExpectation],
) -> list[ReplayExpectation]:
    linked = by_event_id.get(event.id) or []
    if linked:
        return linked
    if event.replay_key and event.replay_key in by_exp_key:
        exp = by_exp_key[event.replay_key]
        if exp.event_id and exp.event_id in by_event_id:
            return by_event_id[exp.event_id]
        return [exp]
    return []


def _rows_for_event(
    event: Event,
    group: list[ReplayExpectation],
    per_event_by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    keys: list[str] = []
    if event.replay_key:
        keys.append(str(event.replay_key))
    keys.extend(exp.key for exp in group if exp.key)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        pe = per_event_by_key.get(key)
        if pe is not None and key not in seen:
            rows.append(pe)
            seen.add(key)
    return rows


def build_feed_verdicts(
    session: Session,
    *,
    alerts: list[Alert] | None = None,
    eval_row: ReplayEvaluation | None = None,
) -> dict[str, dict[str, str]]:
    """``alert_id → {verdict, reason}`` for overview badges.

    Loads the latest agent eval + all expectations once, then resolves each alert
    in memory (accuracy checked at most once per event). Pass ``alerts`` to limit
    work to the overview feed slice; omit to cover every agent alert (store/rescore).
    """
    row = eval_row if eval_row is not None else _latest_agent_eval(session)
    per_event_by_key = _per_event_by_key_from_row(row)
    by_event_id, by_exp_key = _expectation_indexes(session)

    if alerts is None:
        alerts = list(session.scalars(select(Alert).where(Alert.kind == "agent")).all())
    else:
        alerts = [a for a in alerts if (getattr(a, "kind", None) or "agent") != "metric"]

    event_ids = {a.event_id for a in alerts if a.event_id}
    events = (
        {e.id: e for e in session.scalars(select(Event).where(Event.id.in_(event_ids))).all()}
        if event_ids
        else {}
    )

    accuracy_cache: dict[str, bool] = {}
    out: dict[str, dict[str, str]] = {}
    for alert in alerts:
        if not alert.event_id:
            continue
        event = events.get(alert.event_id)
        if event is None:
            out[alert.id] = {
                "verdict": "not yet scored",
                "reason": _VERDICT_REASONS["not yet scored"],
            }
            continue
        group = _group_for_event(event, by_event_id=by_event_id, by_exp_key=by_exp_key)
        rows = _rows_for_event(event, group, per_event_by_key)
        verdict, reason = _verdict_from_rows(
            session,
            event=event,
            rows=rows,
            group=group,
            accuracy_cache=accuracy_cache,
        )
        if verdict is None:
            continue
        out[alert.id] = {"verdict": verdict, "reason": reason or ""}
    return out


def stored_feed_verdicts(row: ReplayEvaluation | None) -> dict[str, dict[str, str]]:
    """Read precomputed map from ``alert_counts.feed_verdicts`` if present."""
    if row is None or not isinstance(row.alert_counts, dict):
        return {}
    raw = row.alert_counts.get(FEED_VERDICTS_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for alert_id, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        verdict = payload.get("verdict")
        if not verdict:
            continue
        out[str(alert_id)] = {
            "verdict": str(verdict),
            "reason": str(payload.get("reason") or ""),
        }
    return out


def attach_feed_verdicts(session: Session, row: ReplayEvaluation) -> dict[str, dict[str, str]]:
    """Compute + persist feed verdicts on ``row.alert_counts``; return the map."""
    verdicts = build_feed_verdicts(session, eval_row=row)
    counts = dict(row.alert_counts or {})
    counts[FEED_VERDICTS_KEY] = verdicts
    row.alert_counts = counts
    session.add(row)
    return verdicts


def overview_feed_verdicts(session: Session, alerts: list[Alert]) -> dict[str, dict[str, str]]:
    """Fast path for overview: prefer stored map; else build once for this feed.

    When the stored map is missing or stale, we batch-build for the feed slice and
    write the merged map back onto the latest agent eval so the next request is
    an O(1) JSON lookup (no per-alert expectation walks).
    """
    row = _latest_agent_eval(session)
    stored = stored_feed_verdicts(row)
    needed = [a for a in alerts if (getattr(a, "kind", None) or "agent") != "metric" and a.event_id]
    if stored and all(a.id in stored for a in needed):
        return stored
    # Legacy eval rows (or new alerts since last rescore): batch-build for this slice.
    built = build_feed_verdicts(session, alerts=needed, eval_row=row)
    merged = {**stored, **built}
    if row is not None and merged:
        counts = dict(row.alert_counts or {})
        counts[FEED_VERDICTS_KEY] = merged
        row.alert_counts = counts
        session.add(row)
    return merged
