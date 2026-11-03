"""Cloud job: add companies by ticker (SEC resolve → XBRL → wall → snapshot).

Usage (from backend/):
    python -m scripts.onboard_job --ticker MSFT
    python -m scripts.onboard_job --ticker JPM --ticker JNJ --ticker WMT
    python -m scripts.onboard_job --ticker T --no-insight
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from datetime import date

from app.config import get_settings
from app.db import SessionLocal
from app.metrics.metric_alerts import scan_metric_alerts
from app.onboard import OnboardError, onboard_ticker


async def _run_one(ticker: str, *, as_of: date | None, insight: bool) -> int:
    settings = get_settings()
    if not (settings.sec_user_agent or "").strip():
        print("onboard: SEC_USER_AGENT is required", file=sys.stderr)
        return 2
    with SessionLocal() as session:
        try:
            result = await onboard_ticker(
                session,
                ticker,
                settings=settings,
                as_of=as_of,
                generate_insight=insight,
            )
            alert_stats = scan_metric_alerts(
                session, as_of=as_of or date.today(), issuer_keys=[result.issuer_key]
            )
            session.commit()
        except OnboardError as exc:
            session.rollback()
            print(f"onboard: {ticker}: refused: {exc}", file=sys.stderr)
            return 1
        except Exception:
            session.rollback()
            traceback.print_exc()
            return 2
    print(f"onboard: {result.message}")
    print(
        f"onboard: key={result.issuer_key} cik={result.cik} sector={result.sector} "
        f"created={result.created} wall_buckets={result.wall_buckets} "
        f"snapshot={result.snapshot_status} cost_usd={result.cost_usd} "
        f"elapsed_s={result.elapsed_seconds}"
    )
    if result.missing:
        print("onboard: missing fields:")
        for m in result.missing:
            print(f"  - {m['field']}: {m['reason']}")
    print(
        f"onboard: metric_alerts created={alert_stats.get('created', 0)} "
        f"skipped={alert_stats.get('skipped', 0)}"
    )
    return 0


async def _run(tickers: list[str], *, as_of: date | None, insight: bool) -> int:
    worst = 0
    for ticker in tickers:
        code = await _run_one(ticker, as_of=as_of, insight=insight)
        worst = max(worst, code)
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Onboard issuer(s) by ticker")
    parser.add_argument(
        "--ticker",
        action="append",
        default=None,
        help="Equity ticker, e.g. MSFT (repeatable; prefer --tickers for Cloud Run)",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help=(
            "Ticker list for Cloud Run: prefer semicolons "
            "(gcloud --args splits on commas), e.g. JPM;JNJ;WMT. Commas also accepted."
        ),
    )
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD cutoff (default: today)")
    parser.add_argument(
        "--no-insight",
        action="store_true",
        help="Skip snapshot generation (metrics + wall only)",
    )
    args = parser.parse_args(argv)
    tickers: list[str] = list(args.ticker or [])
    if args.tickers:
        import re

        tickers.extend(t.strip() for t in re.split(r"[,;]+", args.tickers) if t.strip())
    # De-dupe preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tickers:
        u = t.upper()
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)
    if not ordered:
        parser.error("provide --ticker and/or --tickers")
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    return asyncio.run(_run(ordered, as_of=as_of, insight=not args.no_insight))


if __name__ == "__main__":
    raise SystemExit(main())
