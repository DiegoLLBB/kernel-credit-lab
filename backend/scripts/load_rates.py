"""Load Treasury and FX RateObservation rows from FRED (idempotent).

Usage (from backend/):
    python -m scripts.load_rates

Series: DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DEXUSEU, DEXUSUK, DEXCAUS.
Window: 2025-12-01 → today (inclusive). Pass ``--end`` to override.
Requires FRED_API_KEY (env or .env).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.sources.fred import SUPPORTED_SERIES, FredClient, FredError

DEFAULT_START = date(2025, 12, 1)


def default_end() -> date:
    """Inclusive end of the FRED window: today (never a fixed past cutoff)."""
    return date.today()


def _load_dotenv_candidates() -> None:
    """Populate os.environ from nearby .env files without overriding existing vars."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / ".env", here.parents[2] / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def load_rates(
    *,
    start: date = DEFAULT_START,
    end: date | None = None,
    series: tuple[str, ...] = SUPPORTED_SERIES,
    client: FredClient | None = None,
    session: Session | None = None,
) -> dict[str, int]:
    """Fetch and upsert observations; returns ``{series_id: row_count}``."""
    end = end or default_end()
    if end < start:
        raise FredError(f"FRED window end {end.isoformat()} is before start {start.isoformat()}")
    close_client = False
    close_session = False
    fred = client
    if fred is None:
        fred = FredClient(settings=get_settings())
        close_client = True
    db = session
    if db is None:
        db = SessionLocal()
        close_session = True
    counts: dict[str, int] = {}
    try:
        for series_id in series:
            observations = fred.series_observations(series_id, start, end)
            rows = fred.upsert_observations(db, series_id, observations)
            counts[series_id] = len(rows)
        db.commit()
    finally:
        if close_session:
            db.close()
        if close_client:
            fred.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=DEFAULT_START,
        help=f"Inclusive start (default {DEFAULT_START.isoformat()})",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Inclusive end (default: today)",
    )
    args = parser.parse_args(argv)
    end = args.end or default_end()

    _load_dotenv_candidates()
    get_settings.cache_clear()

    try:
        counts = load_rates(start=args.start, end=end)
    except FredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    total = sum(counts.values())
    for series_id, n in counts.items():
        print(f"{series_id}: {n} observations")
    print(f"total: {total} RateObservation rows upserted ({args.start} → {end})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
