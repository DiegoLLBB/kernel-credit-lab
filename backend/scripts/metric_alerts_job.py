"""Backfill / refresh code-side metric alerts from stored XBRL metrics.

Usage:
    python -m scripts.metric_alerts_job
    python -m scripts.metric_alerts_job --issuer amazon --as-of 2026-11-03
"""

from __future__ import annotations

import argparse
from datetime import date

from app.db import SessionLocal
from app.metrics.metric_alerts import scan_metric_alerts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan credit metrics for band/due_12m alerts")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--issuer", action="append", default=None, dest="issuers")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        stats = scan_metric_alerts(session, as_of=as_of, issuer_keys=args.issuers)
        session.commit()
    print(
        f"metric_alerts: issuers={stats['issuers']} created={stats['created']} "
        f"skipped={stats['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
