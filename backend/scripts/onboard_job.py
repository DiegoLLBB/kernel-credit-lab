"""Cloud job: add a company by ticker (SEC resolve → XBRL → wall → snapshot).

Usage (from backend/):
    python -m scripts.onboard_job --ticker MSFT
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
from app.onboard import OnboardError, onboard_ticker


async def _run(ticker: str, *, as_of: date | None, insight: bool) -> int:
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
            session.commit()
        except OnboardError as exc:
            session.rollback()
            print(f"onboard: refused: {exc}", file=sys.stderr)
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
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Onboard an issuer by ticker")
    parser.add_argument("--ticker", required=True, help="Equity ticker, e.g. MSFT")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD cutoff (default: today)")
    parser.add_argument(
        "--no-insight",
        action="store_true",
        help="Skip snapshot generation (metrics + wall only)",
    )
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    return asyncio.run(_run(args.ticker, as_of=as_of, insight=not args.no_insight))


if __name__ == "__main__":
    raise SystemExit(main())
