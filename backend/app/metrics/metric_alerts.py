"""Code-side metric alerts (risk-band crosses and due_12m jumps) — not the agent."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.charts.crosscheck import accession_url
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


def _series(session: Session, issuer_id: str, metric: str, as_of: date) -> list[CreditMetric]:
    since = as_of - timedelta(days=_LOOKBACK_DAYS)
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

    Idempotent via materiality_reasons dedupe keys. Backfills from the last ~4
    quarters of filed metric rows.
    """
    as_of = as_of or date.today()
    stats = {"created": 0, "skipped": 0, "issuers": 0}
    q = select(Issuer).where(Issuer.parent_id.is_(None))
    if issuer_keys:
        q = q.where(Issuer.key.in_(issuer_keys))
    issuers = list(session.scalars(q).all())
    for issuer in issuers:
        stats["issuers"] += 1
        existing = _existing_keys(session, issuer.id)
        bank = is_bank_sector(issuer.sector)

        # Risk-band crosses for ratio metrics.
        for metric in _RATIO_BAND_METRICS:
            if bank and metric in BANK_NA_METRICS:
                continue
            rows = _series(session, issuer.id, metric, as_of)
            full = _full_series(session, issuer.id, metric, as_of)
            for row in rows:
                if row.value is None:
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
                summary = (
                    f"{label} moved from {before_flag} to {after_flag} "
                    f"({prior.fiscal_label}: {prior.value} → {row.fiscal_label}: {row.value})"
                )
                if link:
                    summary = f"{summary}. Filing: {link}"
                session.add(
                    Alert(
                        issuer_id=issuer.id,
                        kind="metric",
                        title=(
                            f"{issuer.name}: {label} {before_flag} → {after_flag} "
                            f"({row.fiscal_label})"
                        ),
                        summary=summary,
                        before_metrics={
                            "metric": metric,
                            "value": float(prior.value),
                            "flag": before_flag,
                            "period": prior.fiscal_label,
                            "period_end": prior.period_end.isoformat(),
                        },
                        after_metrics={
                            "metric": metric,
                            "value": float(row.value),
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

        # due_12m up ≥25% YoY.
        due_rows = _series(session, issuer.id, "due_12m", as_of)
        due_full = _full_series(session, issuer.id, "due_12m", as_of)
        for row in due_rows:
            if row.value is None:
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
            summary = (
                f"Debt due in 12 months rose {pct:.1f}% "
                f"({prior.fiscal_label}: {prior.value} → {row.fiscal_label}: {row.value})"
            )
            if link:
                summary = f"{summary}. Filing: {link}"
            session.add(
                Alert(
                    issuer_id=issuer.id,
                    kind="metric",
                    title=(
                        f"{issuer.name}: debt due in 12 months up {pct:.1f}% ({row.fiscal_label})"
                    ),
                    summary=summary,
                    before_metrics={
                        "metric": "due_12m",
                        "value": float(prior.value),
                        "period": prior.fiscal_label,
                        "period_end": prior.period_end.isoformat(),
                    },
                    after_metrics={
                        "metric": "due_12m",
                        "value": float(row.value),
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
