"""Load XBRL credit metrics into credit_metric (idempotent).

Usage (from backend/):
    python -m scripts.load_metrics [--as-of YYYY-MM-DD] [--issuer KEY ...]

Fetches each configured issuer's companyfacts from the live SEC API (same
User-Agent, rate limit and retry rules as EDGAR ingestion) and upserts metric
rows. Re-running changes nothing unless the tagged facts changed. Requires
SEC_USER_AGENT (env or .env); refuses to run without it.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.metrics.loader import load_metrics
from app.sources.xbrl import XbrlClient


def _load_dotenv_candidates() -> None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / ".env", here.parents[2] / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_metrics_live(
    *,
    as_of: date | None = None,
    issuer_keys: list[str] | None = None,
    session: Session | None = None,
) -> dict[str, int]:
    """Fetch live companyfacts and upsert metrics (seam for the seed job)."""
    settings = get_settings()
    client = XbrlClient(user_agent=settings.sec_user_agent)

    def fetch(cik: str) -> dict[str, Any]:
        return client.company_facts(cik)

    own = session is None
    db = session or SessionLocal()
    try:
        stats = load_metrics(db, fetch, as_of=as_of, issuer_keys=issuer_keys)
        if own:
            db.commit()
        return stats
    finally:
        if own:
            db.close()


def main(argv: list[str] | None = None) -> int:
    _load_dotenv_candidates()
    parser = argparse.ArgumentParser(description="Load XBRL credit metrics.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--issuer", action="append", default=None, dest="issuers")
    args = parser.parse_args(argv)
    stats = load_metrics_live(as_of=args.as_of, issuer_keys=args.issuers)
    print(
        f"metrics: issuers {stats['issuers']}, rows +{stats['rows_created']}/"
        f"~{stats['rows_updated']}, skipped {stats['skipped']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
