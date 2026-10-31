"""Cloud seed job: load the year-end snapshot, rates, then XBRL credit metrics.

Runs ``load_seed`` on ``seed/snapshot_2025-12-31.json`` followed by ``load_rates``
and the metrics loader, printing one line per step plus a final summary. Exits
nonzero on failure so the Cloud Run execution shows as failed. Local usage is
unchanged (``make seed``).

Usage (from backend/):
    python -m scripts.seed_job
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    CreditMetric,
    Instrument,
    Issuer,
    MaturitySchedule,
    RateObservation,
    Watchlist,
)
from scripts.load_metrics import load_metrics_live
from scripts.load_rates import load_rates
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"

RowCounts = dict[str, int]


def _row_counts() -> RowCounts:
    with SessionLocal() as session:
        return {
            "issuers": session.scalar(select(func.count()).select_from(Issuer)) or 0,
            "instruments": session.scalar(select(func.count()).select_from(Instrument)) or 0,
            "watchlist": session.scalar(select(func.count()).select_from(Watchlist)) or 0,
            "maturity_schedules": session.scalar(select(func.count()).select_from(MaturitySchedule))
            or 0,
            "credit_metrics": session.scalar(select(func.count()).select_from(CreditMetric)) or 0,
            "rate_observations": session.scalar(select(func.count()).select_from(RateObservation))
            or 0,
        }


def main(argv: list[str] | None = None) -> int:
    del argv
    started = datetime.now()
    try:
        before = _row_counts()
        print(
            f"seed: database holds {before['issuers']} issuers, "
            f"{before['instruments']} instruments before load"
        )
        with SessionLocal() as session:
            stats = load_seed(session, Path(SEED_PATH))
            session.commit()
        print(
            f"seed: load_seed {SEED_PATH.name}: "
            f"issuers +{stats.issuers_created}/~{stats.issuers_updated}, "
            f"instruments +{stats.instruments_created}/~{stats.instruments_updated}, "
            f"watchlist +{stats.watchlist_created}, "
            f"maturity_schedules +{stats.maturity_schedules_created}"
            f"/~{stats.maturity_schedules_updated}"
        )
        counts = load_rates()
        total = sum(counts.values())
        for series_id, n in counts.items():
            print(f"seed: load_rates {series_id}: {n} observations")
        with SessionLocal() as session:
            metric_stats = load_metrics_live(session=session)
            session.commit()
        print(
            f"seed: load_metrics: issuers {metric_stats['issuers']}, "
            f"rows +{metric_stats['rows_created']}/~{metric_stats['rows_updated']}, "
            f"skipped {metric_stats['skipped']}"
        )
        after = _row_counts()
        elapsed = (datetime.now() - started).total_seconds()
        print(
            f"seed: complete in {elapsed:.1f}s: "
            f"issuers {before['issuers']} -> {after['issuers']}, "
            f"instruments {before['instruments']} -> {after['instruments']}, "
            f"credit_metrics {before['credit_metrics']} -> {after['credit_metrics']}, "
            f"rate_observations {before['rate_observations']} -> {after['rate_observations']} "
            f"(+{total} upserted), cost_usd 0.000000 (no model calls)"
        )
    except Exception as exc:
        traceback.print_exc()
        print(f"seed: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
