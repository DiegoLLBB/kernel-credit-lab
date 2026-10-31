"""Refresh an issuer's metrics when a new 10-Q/10-K is processed.

Called by the replay runner after it finishes an event whose source filings
include a 10-Q or 10-K, and by live mode at the same point. The refresh is
best-effort and never fails the event: without network access (tests, offline
replay) the SEC fetch raises and the caller logs a skip line.

The point-in-time cutoff is the filing's filed date, so a replay processing
events in order only ever sees what was knowable then.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.metrics.loader import load_metrics


def refresh_issuer_metrics(
    session: Any,
    issuer_key: str,
    filed_on: date,
    *,
    fetch: Any = None,
    config_path: Any = None,
    config: Any = None,
) -> dict[str, int]:
    """Recompute one issuer's metrics with datapoints filed ≤ ``filed_on``."""
    if fetch is None:
        from app.config import get_settings
        from app.sources.xbrl import XbrlClient

        settings = get_settings()
        client = XbrlClient(user_agent=settings.sec_user_agent)

        def fetch(cik: str) -> dict[str, Any]:
            return client.company_facts(cik)

    return load_metrics(
        session,
        fetch,
        config=config,
        config_path=config_path,
        as_of=filed_on,
        issuer_keys=[issuer_key],
    )
