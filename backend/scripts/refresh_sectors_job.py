"""Re-stamp issuer sectors from the GICS map and rescan metric alerts.

Usage:
    python -m scripts.refresh_sectors_job
"""

from __future__ import annotations

import argparse
from datetime import date

from app.db import SessionLocal
from app.metrics.metric_alerts import scan_metric_alerts
from app.onboard.sectors import apply_sector_map, sector_map_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh GICS sectors + metric alerts")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        sector_stats = apply_sector_map(session)
        alert_stats = scan_metric_alerts(session, as_of=as_of)
        session.commit()
    print(f"refresh_sectors: source={sector_map_source()!r} {sector_stats}")
    print(
        f"metric_alerts: issuers={alert_stats['issuers']} created={alert_stats['created']} "
        f"skipped={alert_stats['skipped']} purged={alert_stats.get('purged', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
