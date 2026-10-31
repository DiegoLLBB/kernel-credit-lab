"""Computed-facts JSON for insight generation (the only thing models may quote).

``build_snapshot_facts`` assembles, for an issuer at a date: latest credit
metrics with their one-year-ago comparison, maturity-wall buckets with changes
vs the previous schedule, and recent alerts with their evidence quotes plus
before/after numbers. ``build_impact_facts`` does the same per alert. Every
number the model writes must resolve to a numeric leaf of this JSON (see
:mod:`app.insight.numbers`); change percentages are precomputed here so the
model never does its own arithmetic.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.metrics.compute import METRIC_IDS
from app.models import Alert, CreditMetric, Event, Issuer, MaturitySchedule

_YEAR_DAYS = 365


def _num(value: Any) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)))


def _change_pct(now: float | None, then: float | None) -> float | None:
    if now is None or then is None or then == 0:
        return None
    return round((now - then) / abs(then) * 100, 1)


def _metric_facts(session: Any, issuer_id: str, as_of: date) -> dict[str, Any]:
    rows = session.scalars(
        select(CreditMetric)
        .where(CreditMetric.issuer_id == issuer_id, CreditMetric.filed <= as_of)
        .order_by(CreditMetric.period_end.asc())
    ).all()
    by_metric: dict[str, list[Any]] = {}
    for row in rows:
        by_metric.setdefault(row.metric, []).append(row)
    out: dict[str, Any] = {}
    for metric in METRIC_IDS:
        series = by_metric.get(metric, [])
        if not series:
            out[metric] = {"value": None, "missing": "no data yet"}
            continue
        latest = series[-1]
        prior = None
        cutoff = latest.period_end - timedelta(days=_YEAR_DAYS)
        for row in reversed(series[:-1]):
            if row.period_end <= cutoff:
                prior = row
                break
        now_val, then_val = _num(latest.value), _num(prior.value) if prior else None
        detail = dict(latest.detail or {})
        pct = _change_pct(now_val, then_val)
        out[metric] = {
            "value": now_val,
            "unit": latest.unit,
            "period": latest.fiscal_label,
            "period_end": latest.period_end.isoformat(),
            "filed": latest.filed.isoformat() if latest.filed else None,
            "missing": detail.pop("missing", None),
            "previous_value": then_val,
            "previous_period": prior.fiscal_label if prior else None,
            # change_pct is signed (negative = down). change_pct_abs lets prose
            # write "down 5.4%" for -5.4; direction stays the verifier's job.
            "change_pct": pct,
            "change_pct_abs": abs(pct) if pct is not None else None,
            "cash_only": detail.get("cash_only"),
            "wall_disagrees": detail.get("wall_agrees") is False,
            "wall_value": float(detail["wall_due"]) if detail.get("wall_due") else None,
        }
    return out


def _wall_facts(session: Any, issuer_id: str, as_of: date) -> dict[str, Any]:
    schedules = session.scalars(
        select(MaturitySchedule)
        .where(MaturitySchedule.issuer_id == issuer_id, MaturitySchedule.as_of <= as_of)
        .order_by(MaturitySchedule.as_of.desc())
        .limit(2)
    ).all()
    if not schedules:
        return {"buckets": [], "changes": []}
    current = {b.get("label"): _num(b.get("amount")) for b in schedules[0].buckets or []}
    buckets = [{"label": label, "amount": amount} for label, amount in current.items()]
    changes: list[dict[str, Any]] = []
    if len(schedules) == 2:
        prev = {b.get("label"): _num(b.get("amount")) for b in schedules[1].buckets or []}
        for label, amount in current.items():
            if label in prev and prev[label] != amount:
                delta = (
                    (amount - prev[label])
                    if amount is not None and prev[label] is not None
                    else None
                )
                changes.append(
                    {
                        "label": label,
                        "before": prev[label],
                        "after": amount,
                        "delta": delta,
                        "delta_abs": abs(delta) if delta is not None else None,
                    }
                )
    return {
        "as_of": schedules[0].as_of.isoformat(),
        "previous_as_of": schedules[1].as_of.isoformat() if len(schedules) == 2 else None,
        "buckets": buckets,
        "changes": changes,
    }


def _alert_facts(session: Any, alert: Alert, *, max_findings: int = 3) -> dict[str, Any]:
    from app.api.alerts import _applied_changes, _findings_out

    event = session.get(Event, alert.event_id) if alert.event_id else None
    findings = []
    for finding in _findings_out(session, alert.findings)[:max_findings]:
        quotes = [c.exact_quote for c in finding.citations[:2] if c.exact_quote]
        findings.append({"statement": finding.statement, "quotes": quotes})
    before_after = []
    for change in _applied_changes(session, alert.run_id):
        before, after = _num(change.before), _num(change.after)
        if before is None and after is None:
            continue
        before_after.append(
            {
                "instrument": change.instrument_key,
                "field": change.field,
                "before": before,
                "after": after,
            }
        )
    return {
        "alert_id": alert.id,
        "title": alert.title,
        "event_date": event.event_date.isoformat() if event and event.event_date else None,
        "event_type": event.event_type if event else None,
        "before_after": before_after,
        "findings": findings,
    }


def build_snapshot_facts(session: Any, issuer_key: str, as_of: date) -> dict[str, Any]:
    """Facts JSON for the issuer snapshot headline + 3–4 sentences."""
    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise ValueError(f"unknown issuer: {issuer_key}")
    alerts = session.scalars(
        select(Alert)
        .where(Alert.issuer_id == issuer.id)
        .order_by(Alert.created_at.desc())
        .limit(10)
    ).all()
    visible = []
    for alert in alerts:
        event = session.get(Event, alert.event_id) if alert.event_id else None
        if event is not None and event.event_date > as_of:
            continue
        visible.append(_alert_facts(session, alert))
    return {
        "issuer": issuer.name,
        "issuer_key": issuer.key,
        "ticker": issuer.ticker,
        "as_of": as_of.isoformat(),
        "metrics": _metric_facts(session, issuer.id, as_of),
        "wall": _wall_facts(session, issuer.id, as_of),
        "alerts": visible,
    }


def build_impact_facts(session: Any, alert_id: str) -> dict[str, Any]:
    """Facts JSON for one alert's impact sentence."""
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"unknown alert: {alert_id}")
    issuer = session.get(Issuer, alert.issuer_id)
    facts = _alert_facts(session, alert, max_findings=5)
    facts["issuer"] = issuer.name if issuer else None
    facts["issuer_key"] = issuer.key if issuer else None
    return facts
