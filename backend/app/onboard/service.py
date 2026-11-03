"""Orchestrate ticker onboarding: resolve → issuer → metrics → wall → snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.metrics.config import IssuerMetricConfig, MetricsConfig, entry_to_issuer_config
from app.metrics.loader import load_metrics, wall_for_issuer
from app.models import Issuer, MaturitySchedule, Watchlist
from app.onboard.maturity import WALL_NOTE, build_maturity_buckets
from app.onboard.sic import sector_from_sic
from app.onboard.tags import build_xbrl_config
from app.onboard.tickers import TickerClient
from app.sources.xbrl import XbrlClient


class OnboardError(RuntimeError):
    """User-facing onboard failure (missing debt, unknown ticker, …)."""


def issuer_key_for_ticker(ticker: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (ticker or "").strip().lower()).strip("_")
    if not key:
        raise OnboardError("ticker is required")
    return key


@dataclass
class OnboardResult:
    ticker: str
    issuer_key: str
    cik: str
    name: str
    sector: str
    created: bool
    metrics_created: int = 0
    metrics_updated: int = 0
    missing: list[dict[str, str]] = field(default_factory=list)
    wall_buckets: int = 0
    wall_as_of: date | None = None
    snapshot_status: str | None = None
    monitor_from: date | None = None
    cost_usd: float | None = None
    elapsed_seconds: float | None = None
    message: str = ""


def _upsert_watchlist(session: Session, issuer: Issuer, *, monitor_from: date) -> Watchlist:
    row = session.scalar(select(Watchlist).where(Watchlist.issuer_id == issuer.id))
    if row is None:
        row = Watchlist(
            issuer_id=issuer.id,
            label=issuer.ticker or issuer.key,
            monitor_from=monitor_from,
        )
        session.add(row)
    else:
        # Keep an existing earlier floor; never widen into a backfill window.
        if row.monitor_from is None or row.monitor_from > monitor_from:
            row.monitor_from = monitor_from
        if not row.label:
            row.label = issuer.ticker or issuer.key
    session.flush()
    return row


def _upsert_maturity(
    session: Session,
    issuer: Issuer,
    *,
    as_of: date,
    buckets: list[dict[str, Any]],
    source_url: str,
) -> MaturitySchedule:
    row = session.scalar(
        select(MaturitySchedule).where(
            MaturitySchedule.issuer_id == issuer.id,
            MaturitySchedule.as_of == as_of,
        )
    )
    if row is None:
        row = MaturitySchedule(
            issuer_id=issuer.id,
            basis="consolidated",
            as_of=as_of,
            source_url=source_url,
            buckets=buckets,
        )
        session.add(row)
    else:
        row.buckets = buckets
        row.source_url = source_url
        row.basis = "consolidated"
    session.flush()
    return row


async def onboard_ticker(
    session: Session,
    ticker: str,
    *,
    settings: Settings | None = None,
    tickers: TickerClient | None = None,
    xbrl: XbrlClient | None = None,
    as_of: date | None = None,
    generate_insight: bool = True,
) -> OnboardResult:
    """Resolve ticker, create/update issuer, load metrics + wall + snapshot.

    Idempotent: re-running the same ticker refreshes metrics/wall/snapshot and
    does not create a duplicate issuer. Refuses when companyfacts lack usable
    debt tags. Adds the issuer to the live watchlist with monitor_from=today so
    the next scheduled live run sees only new filings (no agent backfill).
    """
    started = datetime.now(UTC)
    settings = settings or get_settings()
    cutoff = as_of or date.today()
    tickers = tickers or TickerClient(user_agent=settings.sec_user_agent)
    xbrl = xbrl or XbrlClient(user_agent=settings.sec_user_agent)

    try:
        match = tickers.resolve(ticker)
    except KeyError as exc:
        raise OnboardError(str(exc)) from exc
    except ValueError as exc:
        raise OnboardError(str(exc)) from exc

    key = issuer_key_for_ticker(match.ticker)
    try:
        submissions = tickers.submissions(match.cik)
    except Exception:
        submissions = {}
    sic = submissions.get("sic")
    sic_desc = submissions.get("sicDescription")
    sector = sector_from_sic(sic, sic_desc)

    try:
        facts = xbrl.company_facts(match.cik)
    except Exception as exc:
        raise OnboardError(f"Failed to fetch companyfacts for {match.ticker}: {exc}") from exc

    try:
        xbrl_entry = build_xbrl_config(facts, cik=match.cik, as_of=cutoff)
    except ValueError as exc:
        raise OnboardError(str(exc)) from exc

    missing = list(xbrl_entry.pop("_missing", []) or [])

    try:
        wall_as_of, buckets, source_url = build_maturity_buckets(facts, as_of=cutoff)
    except ValueError as exc:
        # Debt loaded but no maturity schedule — still refuse wall? Spec says
        # load the 10-K maturity schedule; missing tags shown as missing.
        wall_as_of, buckets, source_url = cutoff, [], None
        missing.append({"field": "maturity_schedule", "reason": str(exc)})

    issuer = session.scalar(select(Issuer).where(Issuer.key == key))
    created = False
    if issuer is None:
        # Also match by CIK if a seed row already exists under another key.
        issuer = session.scalar(select(Issuer).where(Issuer.cik == match.cik))
    if issuer is None:
        issuer = Issuer(
            key=key,
            name=match.name,
            cik=match.cik,
            ticker=match.ticker,
            sector=sector,
            xbrl_config=xbrl_entry,
            onboarded_at=started,
            onboard_source="ticker",
        )
        session.add(issuer)
        session.flush()
        created = True
    else:
        issuer.key = issuer.key or key
        issuer.name = match.name
        issuer.cik = match.cik
        issuer.ticker = match.ticker
        issuer.sector = sector or issuer.sector
        issuer.xbrl_config = xbrl_entry
        if issuer.onboarded_at is None:
            issuer.onboarded_at = started
        issuer.onboard_source = issuer.onboard_source or "ticker"
        session.flush()

    monitor_from = cutoff
    _upsert_watchlist(session, issuer, monitor_from=monitor_from)

    if buckets and source_url:
        _upsert_maturity(
            session,
            issuer,
            as_of=wall_as_of,
            buckets=buckets,
            source_url=source_url,
        )
    elif buckets and not source_url:
        from app.sources.xbrl import xbrl_url

        _upsert_maturity(
            session,
            issuer,
            as_of=wall_as_of,
            buckets=buckets,
            source_url=xbrl_url(match.cik),
        )

    # Single-issuer MetricsConfig from the stored entry.
    issuer_cfg = entry_to_issuer_config(key, xbrl_entry)
    cfg = MetricsConfig(issuers={key: issuer_cfg})

    def fetch(_cik: str) -> dict[str, Any]:
        return facts

    stats = load_metrics(
        session,
        fetch,
        config=cfg,
        as_of=cutoff,
        issuer_keys=[key],
        with_wall=True,
    )
    # Ensure wall_for_issuer sees the schedule we just wrote.
    _ = wall_for_issuer(session, issuer.id)

    from app.metrics.peers import BANK_NA_METRICS, BANK_NA_REASON, is_bank_sector

    if is_bank_sector(sector):
        from app.models import CreditMetric

        for row in session.scalars(
            select(CreditMetric).where(
                CreditMetric.issuer_id == issuer.id,
                CreditMetric.metric.in_(tuple(BANK_NA_METRICS)),
            )
        ).all():
            row.value = None
            detail = dict(row.detail or {})
            detail["missing"] = BANK_NA_REASON
            row.detail = detail
        # De-dupe missing; ensure every bank-NA metric is listed (never guessed).
        seen_fields: set[str] = set()
        deduped: list[dict[str, str]] = []
        for m in missing:
            f = m.get("field") or ""
            if f in seen_fields:
                continue
            seen_fields.add(f)
            if f in BANK_NA_METRICS:
                deduped.append({"field": f, "reason": BANK_NA_REASON})
            else:
                deduped.append(m)
        for metric in sorted(BANK_NA_METRICS):
            if metric not in seen_fields:
                deduped.append({"field": metric, "reason": BANK_NA_REASON})
                seen_fields.add(metric)
        missing = deduped

    snapshot_status = None
    cost_usd = None
    if generate_insight:
        from app.insight.generate import generate_snapshot

        row = await generate_snapshot(session, key, cutoff)
        snapshot_status = row.status
        # CreditInsight has no cost column; cost stays unknown here (job logs timing).
        cost_usd = None

    elapsed = (datetime.now(UTC) - started).total_seconds()
    msg = (
        f"{'Created' if created else 'Updated'} {match.ticker} ({match.name}); "
        f"metrics +{stats.get('rows_created', 0)}/~{stats.get('rows_updated', 0)}; "
        f"wall {len(buckets)} buckets; "
        f"{len(missing)} missing fields; "
        f"live monitor_from={monitor_from.isoformat()} ({WALL_NOTE})"
    )
    return OnboardResult(
        ticker=match.ticker,
        issuer_key=key,
        cik=match.cik,
        name=match.name,
        sector=sector,
        created=created,
        metrics_created=int(stats.get("rows_created", 0)),
        metrics_updated=int(stats.get("rows_updated", 0)),
        missing=missing,
        wall_buckets=len(buckets),
        wall_as_of=wall_as_of if buckets else None,
        snapshot_status=snapshot_status,
        monitor_from=monitor_from,
        cost_usd=cost_usd,
        elapsed_seconds=elapsed,
        message=msg,
    )


# Silence unused import warning for type checkers that want IssuerMetricConfig.
_ = IssuerMetricConfig
