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

import contextlib
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.insight.display import format_date_display, format_multiple_display, format_usd_display
from app.metrics.compute import METRIC_IDS
from app.models import Alert, CreditMetric, Event, Issuer


def _num(value: Any) -> float | None:
    """Best-effort numeric leaf for before/after JSON (names, "—", dicts → None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        with contextlib.suppress(InvalidOperation, ValueError):
            return float(Decimal(value.strip().replace(",", "")))
        return None
    return None


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
        latest = next((r for r in reversed(series) if r.value is not None), series[-1])
        from app.metrics.periods import prior_same_period

        prior = prior_same_period(series, latest)
        now_val, then_val = _num(latest.value), _num(prior.value) if prior else None
        detail = dict(latest.detail or {})
        pct = _change_pct(now_val, then_val)
        unit = latest.unit or "USD"
        display_value = (
            format_usd_display(now_val)
            if unit == "USD"
            else format_multiple_display(now_val)
            if unit == "x"
            else str(now_val)
            if now_val is not None
            else None
        )
        out[metric] = {
            "value": now_val,
            "value_display": display_value,
            "unit": unit,
            "period": latest.fiscal_label,
            "period_end": latest.period_end.isoformat(),
            "period_end_display": format_date_display(latest.period_end),
            "filed": latest.filed.isoformat() if latest.filed else None,
            "missing": detail.pop("missing", None),
            "previous_value": then_val,
            "previous_value_display": (
                format_usd_display(then_val)
                if unit == "USD" and then_val is not None
                else format_multiple_display(then_val)
                if unit == "x" and then_val is not None
                else None
            ),
            "previous_period": prior.fiscal_label if prior else None,
            "previous_period_end": prior.period_end.isoformat() if prior else None,
            "change_pct": pct,
            "change_pct_abs": abs(pct) if pct is not None else None,
            "cash_only": detail.get("cash_only"),
            "wall_disagrees": detail.get("wall_agrees") is False,
            "wall_value": float(detail["wall_due"]) if detail.get("wall_due") else None,
            "wall_value_display": format_usd_display(detail.get("wall_due")),
            "source_note": detail.get("source_note"),
        }
    return out


def _wall_facts(session: Any, issuer_id: str, as_of: date) -> dict[str, Any]:
    from app.tools.maturity import maturity_wall

    wall = maturity_wall(session, issuer_id, as_of)
    buckets_raw = wall.get("buckets") or {}
    buckets = []
    for label, data in buckets_raw.items():
        total = _num((data or {}).get("total_outstanding"))
        buckets.append(
            {
                "label": label,
                "amount": total,
                "amount_display": format_usd_display(total),
            }
        )
    year_start = date(as_of.year, 1, 1)
    wall_then = maturity_wall(session, issuer_id, year_start)
    then_buckets = (wall_then.get("buckets") or {}) if wall_then else {}
    changes: list[dict[str, Any]] = []
    for label, data in buckets_raw.items():
        amount = _num((data or {}).get("total_outstanding"))
        prev_data = then_buckets.get(label) if isinstance(then_buckets, dict) else None
        prev = _num((prev_data or {}).get("total_outstanding")) if prev_data else None
        if prev is not None and amount is not None and prev != amount:
            delta = amount - prev
            changes.append(
                {
                    "label": label,
                    "before": prev,
                    "after": amount,
                    "before_display": format_usd_display(prev),
                    "after_display": format_usd_display(amount),
                    "delta": delta,
                    "delta_abs": abs(delta),
                    "delta_display": format_usd_display(delta),
                }
            )
    schedule_as_of = wall.get("schedule_as_of")
    if hasattr(schedule_as_of, "isoformat"):
        schedule_as_of = schedule_as_of.isoformat()
    return {
        "as_of": as_of.isoformat(),
        "as_of_display": format_date_display(as_of),
        "schedule_as_of": schedule_as_of,
        "previous_as_of": year_start.isoformat(),
        "buckets": buckets,
        "changes": changes,
    }


def _wall_delta_entries(deltas: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for delta in deltas:
        b, a = _num(delta.get("before")), _num(delta.get("after"))
        if b is None and a is None:
            continue
        diff = (a - b) if b is not None and a is not None else None
        out.append(
            {
                "bucket": delta.get("label"),
                "field": "total_outstanding",
                "before": b,
                "after": a,
                "before_display": format_usd_display(b),
                "after_display": format_usd_display(a),
                "change": diff,
                "change_abs": abs(diff) if diff is not None else None,
                "change_display": format_usd_display(diff),
            }
        )
    return out


def _alert_facts(session: Any, alert: Alert, *, max_findings: int = 3) -> dict[str, Any]:
    from app.api.alerts import _applied_changes, _findings_out
    from app.api.wall_delta import agent_stored_deltas, recomputed_walls

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
                "before_display": format_usd_display(before),
                "after_display": format_usd_display(after),
            }
        )
    # Prefer walls recomputed from current records (day before vs after the change).
    _wall_before, _wall_after, recomputed = recomputed_walls(session, alert)
    wall_deltas = _wall_delta_entries(recomputed)
    before_after.extend(wall_deltas)
    agent_wall_deltas = _wall_delta_entries(agent_stored_deltas(alert))
    return {
        "alert_id": alert.id,
        "title": alert.title,
        "summary": alert.summary,
        "event_date": event.event_date.isoformat() if event and event.event_date else None,
        "event_date_display": format_date_display(event.event_date) if event else None,
        "event_type": event.event_type if event else None,
        "before_after": before_after,
        "wall_deltas": wall_deltas,
        "agent_wall_deltas": agent_wall_deltas,
        "agent_wall_note": "as computed by the agent at the time",
        "findings": findings,
    }


def build_snapshot_facts(session: Any, issuer_key: str, as_of: date) -> dict[str, Any]:
    """Facts JSON for the issuer snapshot headline + 3 sentences."""
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
        "as_of_display": format_date_display(as_of),
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


def build_refinancing_facts(
    session: Any,
    issuer_key: str,
    as_of: date,
    *,
    tenor_years: int = 5,
) -> dict[str, Any]:
    """Facts JSON for the optional +0bp refinancing insight sentence."""
    from app.tools.refinancing import DEFAULT_TENOR_YEARS, refinancing_panel

    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise ValueError(f"unknown issuer: {issuer_key}")
    tenor = tenor_years or DEFAULT_TENOR_YEARS
    panel = refinancing_panel(session, issuer.id, as_of, shock_bps=0, tenor_years=tenor)
    base = panel["base"]
    cov = panel["coverage_base"]
    spread = base.get("spread") or {}
    buckets = []
    for b in base.get("buckets") or []:
        inc = _num(b.get("incremental_annual_interest"))
        buckets.append(
            {
                "label": b.get("label"),
                "incremental_annual_interest": inc,
                "incremental_display": format_usd_display(inc),
                "maturing_coupon_pct": _num(b.get("maturing_coupon_pct")),
                "maturing_coupon_source": b.get("maturing_coupon_source"),
            }
        )
    return {
        "issuer": issuer.name,
        "issuer_key": issuer.key,
        "as_of": as_of.isoformat(),
        "as_of_display": format_date_display(as_of),
        "tenor_years": tenor,
        "shock_bps": 0,
        "derivation_line": base.get("derivation_line"),
        "base_scenario_line": panel.get("base_scenario_line"),
        "spread_pct": _num(spread.get("spread_pct")),
        "treasury_pct": _num((base.get("benchmark") or {}).get("value")),
        "cost_of_debt_pct": _num((spread.get("cost_of_debt") or {}).get("rate_pct")),
        "base_incremental_annual_interest": _num(base.get("total_incremental_annual_interest")),
        "named_incremental_annual_interest": _num(base.get("named_incremental_annual_interest")),
        "estimated_incremental_annual_interest": _num(
            base.get("estimated_incremental_annual_interest")
        ),
        "coverage_before": _num(cov.get("coverage_before")),
        "coverage_after": _num(cov.get("coverage_after")),
        "coverage_missing": cov.get("missing"),
        "missing": base.get("missing"),
        "buckets": buckets,
        "footnote": panel.get("footnote"),
    }
