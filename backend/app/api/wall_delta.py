"""Recompute alert before/after maturity walls from current instrument state."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import bucket_deltas, json_safe
from app.insight.display import format_usd_wall
from app.models import Alert, Event, StateChange
from app.tools.maturity import maturity_wall


def _change_window(session: Session, alert: Alert, event: Event | None) -> tuple[date, date]:
    """Day-before vs day-of for the alert's applied changes (or the event date)."""
    anchor = event.event_date if event and event.event_date else date.today()
    if not alert.run_id:
        return anchor - timedelta(days=1), anchor
    effective_dates: list[date] = []
    for row in session.scalars(
        select(StateChange).where(
            StateChange.run_id == alert.run_id,
            StateChange.status == "applied",
            StateChange.effective_date.is_not(None),
        )
    ).all():
        if row.effective_date is not None:
            effective_dates.append(row.effective_date)
    if not effective_dates:
        return anchor - timedelta(days=1), anchor
    start = min(effective_dates)
    end = max(effective_dates)
    return start - timedelta(days=1), end


def recomputed_walls(
    session: Session, alert: Alert
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, str]]]:
    """Point-in-time walls from current records: day before change vs after."""
    event = session.get(Event, alert.event_id) if alert.event_id else None
    before_day, after_day = _change_window(session, alert, event)
    before = json_safe(maturity_wall(session, alert.issuer_id, before_day))
    after = json_safe(maturity_wall(session, alert.issuer_id, after_day))
    deltas = bucket_deltas(before, after)
    return before, after, deltas


def primary_wall_before_after(
    session: Session, alert: Alert
) -> dict[str, Any] | None:
    """Largest absolute wall move for feed/timeline cards."""
    _before, _after, deltas = recomputed_walls(session, alert)
    best: dict[str, Any] | None = None
    best_abs = -1.0
    for d in deltas:
        try:
            b, a = float(d["before"]), float(d["after"])
        except (TypeError, ValueError, KeyError):
            continue
        magnitude = abs(a - b)
        if magnitude > best_abs:
            best_abs = magnitude
            best = {
                "field": d.get("label") or "total_outstanding",
                "bucket": d.get("label"),
                "before": b,
                "after": a,
                "before_display": format_usd_wall(b),
                "after_display": format_usd_wall(a),
            }
    return best


def agent_stored_deltas(alert: Alert) -> list[dict[str, str]]:
    """Wall deltas the agent recorded at run time (may disagree with recomputed)."""
    before = alert.before_metrics if isinstance(alert.before_metrics, dict) else None
    after = alert.after_metrics if isinstance(alert.after_metrics, dict) else None
    before_issuer = (before or {}).get("issuer") if before else None
    after_issuer = (after or {}).get("issuer") if after else None
    wall_before = before_issuer if isinstance(before_issuer, dict) else before
    wall_after = after_issuer if isinstance(after_issuer, dict) else after
    return bucket_deltas(
        wall_before if isinstance(wall_before, dict) else None,
        wall_after if isinstance(wall_after, dict) else None,
    )
