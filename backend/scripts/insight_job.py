"""Cloud job: generate credit insight snapshots and missing impact sentences.

For every configured issuer, generates the credit snapshot (headline + 3–4
sentences) at --as-of, then fills in impact sentences for alerts that lack
one. Results cache by input hash: re-running without new facts or alerts
makes no model calls. Exits nonzero only when every generation fails.

Usage (from backend/):
    python -m scripts.insight_job [--as-of YYYY-MM-DD] [--issuer KEY ...]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.insight.brief import generate_daily_brief, replay_last_day
from app.insight.generate import generate_impact, generate_refinancing, generate_snapshot
from app.metrics.config import load_metric_config
from app.models import Alert, Issuer
from app.models.credit import CreditInsight


async def _run(as_of: date, issuer_keys: list[str] | None) -> dict[str, int]:
    stats = {"snapshots": 0, "refinancing": 0, "impacts": 0, "briefs": 0, "errors": 0}
    with SessionLocal() as session:
        cfg = load_metric_config()
        keys = issuer_keys or list(cfg.issuers)
        for key in keys:
            issuer = session.scalar(select(Issuer).where(Issuer.key == key))
            if issuer is None:
                print(f"insight: {key}: no such issuer, skipped")
                continue
            try:
                row = await generate_snapshot(session, key, as_of)
                print(f"insight: snapshot {key} @ {as_of}: {row.status}")
                stats["snapshots"] += 1
            except Exception as exc:
                stats["errors"] += 1
                traceback.print_exc()
                print(f"insight: snapshot {key} FAILED: {exc}", file=sys.stderr)
            try:
                row = await generate_refinancing(session, key, as_of)
                print(f"insight: refinancing {key} @ {as_of}: {row.status}")
                stats["refinancing"] += 1
                if row.status == "empty":
                    stats["errors"] += 1
            except Exception as exc:
                stats["errors"] += 1
                traceback.print_exc()
                print(f"insight: refinancing {key} FAILED: {exc}", file=sys.stderr)
        for alert in session.scalars(select(Alert)).all():
            if issuer_keys is not None:
                issuer = session.get(Issuer, alert.issuer_id)
                if issuer is None or issuer.key not in issuer_keys:
                    continue
            exists = session.scalar(
                select(CreditInsight).where(
                    CreditInsight.scope == "impact", CreditInsight.ref == alert.id
                )
            )
            # Retry empty/error rows: a prior attempt may have had incomplete
            # facts (e.g. missing wall deltas) and cached a refusal.
            if exists is not None and exists.status == "complete":
                continue
            try:
                row = await generate_impact(session, alert.id)
                print(f"insight: impact {alert.id[:8]} ({alert.title[:60]}): {row.status}")
                stats["impacts"] += 1
                if row.status == "empty":
                    stats["errors"] += 1
            except Exception as exc:
                stats["errors"] += 1
                traceback.print_exc()
                print(f"insight: impact {alert.id[:8]} FAILED: {exc}", file=sys.stderr)
        # One brief for the replay's last calendar day (and for --as-of when distinct).
        brief_days = {as_of}
        last = replay_last_day(session)
        if last is not None:
            brief_days.add(last)
        for day in sorted(brief_days):
            try:
                row = await generate_daily_brief(session, day)
                print(
                    f"insight: brief @ {day}: {row.status} "
                    f"kept={len(row.sentences or [])} refused={len(row.dropped or [])}"
                )
                stats["briefs"] += 1
            except Exception as exc:
                stats["errors"] += 1
                traceback.print_exc()
                print(f"insight: brief @ {day} FAILED: {exc}", file=sys.stderr)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate checked credit insight text.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--issuer", action="append", default=None, dest="issuers")
    args = parser.parse_args(argv)
    stats = asyncio.run(_run(args.as_of, args.issuers))
    print(
        f"insight: snapshots {stats['snapshots']}, refinancing {stats['refinancing']}, "
        f"impacts {stats['impacts']}, briefs {stats['briefs']}, errors {stats['errors']}"
    )
    return (
        1
        if stats["errors"]
        and not (
            stats["snapshots"] or stats["impacts"] or stats["refinancing"] or stats["briefs"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
