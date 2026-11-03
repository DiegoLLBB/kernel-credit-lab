"""Code-side metric alerts (risk-band crosses and due_12m jumps) — not the agent.

Only filings dated on/after the monitoring window (1 Jan 2026) create alerts.
Titles show before → after values (not percentages above 100%).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.charts.crosscheck import accession_url
from app.insight.display import format_multiple_display, format_usd_display
from app.metrics.peers import BANK_NA_METRICS, is_bank_sector
from app.metrics.periods import prior_same_period
from app.models import Alert, CreditMetric, Issuer

_RATIO_BAND_METRICS = (
    "leverage",
    "net_leverage",
    "interest_coverage",
    "fcf_to_debt",
)
_DUE12_JUMP_PCT = 25.0
_LOOKBACK_DAYS = 400  # ~4 quarters of filed updates
# Live monitoring window: metric alerts only for filings on/after this date.
MONITOR_FROM = date(2026, 1, 1)

_METRIC_LABELS = {
    "leverage": "Debt ÷ EBITDA",
    "net_leverage": "Net debt ÷ EBITDA",
    "interest_coverage": "EBITDA ÷ interest",
    "fcf_to_debt": "Free cash flow ÷ debt",
    "due_12m": "Debt due in 12 months",
}


def _dedupe_key(issuer_id: str, metric: str, period_end: date, reason: str) -> str:
    return f"metric:{issuer_id}:{metric}:{period_end.isoformat()}:{reason}"


def _existing_keys(session: Session, issuer_id: str) -> set[str]:
    keys: set[str] = set()
    for alert in session.scalars(
        select(Alert).where(Alert.issuer_id == issuer_id, Alert.kind == "metric")
    ).all():
        reasons = alert.materiality_reasons or []
        for r in reasons:
            if isinstance(r, str) and r.startswith("metric:"):
                keys.add(r)
            elif isinstance(r, dict) and r.get("dedupe"):
                keys.add(str(r["dedupe"]))
    return keys


def _filing_link(issuer: Issuer, row: CreditMetric) -> str | None:
    return accession_url(issuer.cik, row.accession)


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "—"
    return format_multiple_display(value)


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    return format_usd_display(value)


def _purge_stale_metric_alerts(session: Session) -> int:
    """Delete metric alerts outside the window or with legacy %-in-title wording."""
    removed = 0
    for alert in list(session.scalars(select(Alert).where(Alert.kind == "metric")).all()):
        after = alert.after_metrics if isinstance(alert.after_metrics, dict) else {}
        filed_raw = after.get("filed")
        filed: date | None = None
        if isinstance(filed_raw, str) and filed_raw:
            try:
                filed = date.fromisoformat(filed_raw[:10])
            except ValueError:
                filed = None
        title = alert.title or ""
        legacy_pct = " up " in title and "%" in title
        if filed is None or filed < MONITOR_FROM or legacy_pct or "display" not in after:
            session.delete(alert)
            removed += 1
    return removed


def _series(session: Session, issuer_id: str, metric: str, as_of: date) -> list[CreditMetric]:
    since = max(as_of - timedelta(days=_LOOKBACK_DAYS), MONITOR_FROM)
    return list(
        session.scalars(
            select(CreditMetric)
            .where(
                CreditMetric.issuer_id == issuer_id,
                CreditMetric.metric == metric,
                CreditMetric.filed <= as_of,
                CreditMetric.filed >= since,
            )
            .order_by(CreditMetric.period_end.asc())
        ).all()
    )


def _full_series(session: Session, issuer_id: str, metric: str, as_of: date) -> list[CreditMetric]:
    return list(
        session.scalars(
            select(CreditMetric)
            .where(
                CreditMetric.issuer_id == issuer_id,
                CreditMetric.metric == metric,
                CreditMetric.filed <= as_of,
            )
            .order_by(CreditMetric.period_end.asc())
        ).all()
    )


def scan_metric_alerts(
    session: Session,
    *,
    as_of: date | None = None,
    issuer_keys: list[str] | None = None,
) -> dict[str, int]:
    """Create metric alerts for band crosses and due_12m ≥25% YoY jumps.

    Idempotent via materiality_reasons dedupe keys. Only rows with
    ``filed >= MONITOR_FROM`` (1 Jan 2026) create alerts; older metric alerts
    are purged.
    """
    as_of = as_of or date.today()
    stats = {"created": 0, "skipped": 0, "issuers": 0, "purged": 0}
    stats["purged"] = _purge_stale_metric_alerts(session)
    q = select(Issuer).where(Issuer.parent_id.is_(None))
    if issuer_keys:
        q = q.where(Issuer.key.in_(issuer_keys))
    issuers = list(session.scalars(q).all())
    for issuer in issuers:
        stats["issuers"] += 1
        existing = _existing_keys(session, issuer.id)
        bank = is_bank_sector(issuer.sector)

        for metric in _RATIO_BAND_METRICS:
            if bank and metric in BANK_NA_METRICS:
                continue
            rows = _series(session, issuer.id, metric, as_of)
            full = _full_series(session, issuer.id, metric, as_of)
            for row in rows:
                if row.value is None or row.filed is None or row.filed < MONITOR_FROM:
                    continue
                prior = prior_same_period(full, row)
                if prior is None or prior.value is None:
                    continue
                from app.api.credit import flag_for

                before_flag = flag_for(metric, prior.value, sector=issuer.sector)
                after_flag = flag_for(metric, row.value, sector=issuer.sector)
                if not before_flag or not after_flag or before_flag == after_flag:
                    continue
                reason = "band_cross"
                key = _dedupe_key(issuer.id, metric, row.period_end, reason)
                if key in existing:
                    stats["skipped"] += 1
                    continue
                label = _METRIC_LABELS.get(metric, metric)
                link = _filing_link(issuer, row)
                before_v = float(prior.value)
                after_v = float(row.value)
                before_disp = _fmt_ratio(before_v)
                after_disp = _fmt_ratio(after_v)
                summary = (
                    f"{label}: {before_disp} → {after_disp} "
                    f"({before_flag} → {after_flag}; {prior.fiscal_label} → {row.fiscal_label})"
                )
                if link:
                    summary = f"{summary}. Filing: {link}"
                session.add(
                    Alert(
                        issuer_id=issuer.id,
                        kind="metric",
                        title=(
                            f"{issuer.name}: {label} {before_disp} → {after_disp} "
                            f"({row.fiscal_label})"
                        ),
                        summary=summary,
                        before_metrics={
                            "metric": metric,
                            "value": before_v,
                            "display": before_disp,
                            "flag": before_flag,
                            "period": prior.fiscal_label,
                            "period_end": prior.period_end.isoformat(),
                        },
                        after_metrics={
                            "metric": metric,
                            "value": after_v,
                            "display": after_disp,
                            "flag": after_flag,
                            "period": row.fiscal_label,
                            "period_end": row.period_end.isoformat(),
                            "filed": row.filed.isoformat() if row.filed else None,
                            "accession": row.accession,
                            "filing_url": link,
                        },
                        materiality_reasons=[key, reason, f"flag:{before_flag}->{after_flag}"],
                        status="new",
                    )
                )
                existing.add(key)
                stats["created"] += 1

        due_rows = _series(session, issuer.id, "due_12m", as_of)
        due_full = _full_series(session, issuer.id, "due_12m", as_of)
        for row in due_rows:
            if row.value is None or row.filed is None or row.filed < MONITOR_FROM:
                continue
            prior = prior_same_period(due_full, row)
            if prior is None or prior.value is None:
                continue
            from app.api.credit import _change_pct

            pct = _change_pct(row.value, prior.value)
            if pct is None or pct < _DUE12_JUMP_PCT:
                continue
            reason = "due_12m_jump"
            key = _dedupe_key(issuer.id, "due_12m", row.period_end, reason)
            if key in existing:
                stats["skipped"] += 1
                continue
            link = _filing_link(issuer, row)
            before_v = float(prior.value)
            after_v = float(row.value)
            before_disp = _fmt_usd(before_v)
            after_disp = _fmt_usd(after_v)
            summary = (
                f"Debt due in 12 months: {before_disp} → {after_disp} "
                f"({prior.fiscal_label} → {row.fiscal_label})"
            )
            if link:
                summary = f"{summary}. Filing: {link}"
            session.add(
                Alert(
                    issuer_id=issuer.id,
                    kind="metric",
                    title=(
                        f"{issuer.name}: debt due in 12 months "
                        f"{before_disp} → {after_disp} ({row.fiscal_label})"
                    ),
                    summary=summary,
                    before_metrics={
                        "metric": "due_12m",
                        "value": before_v,
                        "display": before_disp,
                        "period": prior.fiscal_label,
                        "period_end": prior.period_end.isoformat(),
                    },
                    after_metrics={
                        "metric": "due_12m",
                        "value": after_v,
                        "display": after_disp,
                        "period": row.fiscal_label,
                        "period_end": row.period_end.isoformat(),
                        "change_pct": pct,
                        "filed": row.filed.isoformat() if row.filed else None,
                        "accession": row.accession,
                        "filing_url": link,
                    },
                    materiality_reasons=[key, reason, f"change_pct:{pct}"],
                    status="new",
                )
            )
            existing.add(key)
            stats["created"] += 1
    session.flush()
    return stats
