"""Client for the SEC companyfacts XBRL API (per-company tagged facts).

Mirrors the politeness contract of :mod:`app.sources.edgar` — the SEC requires
identifying traffic (note the mandatory User-Agent), at most 10 requests per
second, and honoring Retry-After on 429/503:

- https://www.sec.gov/os/webmaster-faq (User-Agent + rate guidance)
- https://www.sec.gov/edgar/sec-api-documentation (companyfacts endpoints)

Endpoint shape: ``/api/xbrl/companyfacts/CIK##########.json`` (zero-padded
10-digit CIK, same as EDGAR company concept files).

The returned document is ``{\"cik\", \"entityName\", \"facts\": {taxonomy:
{tag: {\"label\", \"description\", \"units\": {unit: [datapoints...]}}}}}}``
where each datapoint carries ``start``/``end``, ``val``, ``accn``, ``fy``,
``fp``, ``form`` and ``filed``. All metric extraction lives in
:mod:`app.metrics`; this module only fetches bytes/JSON politely.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.sources.edgar import _require_user_agent, pad_cik

XBRL_BASE = "https://data.sec.gov/api/xbrl"
MAX_RETRIES = 5
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


def xbrl_url(cik: str) -> str:
    """Canonical companyfacts URL for a CIK (zero-padded to 10 digits)."""
    return f"{XBRL_BASE}/companyfacts/CIK{pad_cik(cik)}.json"


@dataclass
class XbrlClient:
    """Polite fetcher for SEC companyfacts documents."""

    user_agent: str
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    max_retries: int = MAX_RETRIES
    min_interval: float = 0.12  # keeps us under the SEC's 10 req/s guidance
    _last_call: float = field(default=0.0, init=False, repr=False)

    def _throttle(self) -> None:
        now = self.clock()
        wait = self.min_interval - (now - self._last_call)
        if wait > 0:
            self.sleeper(wait)
            now = self.clock()
        self._last_call = now

    def _get(self, url: str) -> httpx.Response:
        """GET with throttle, retry on transient statuses, honor Retry-After."""
        _require_user_agent(self.user_agent)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                with httpx.Client(transport=self.transport, timeout=30.0) as client:
                    response = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                self.sleeper(2.0 * (attempt + 1))
                continue
            if response.status_code == 200:
                return response
            if response.status_code not in RETRYABLE:
                response.raise_for_status()
            last_error = httpx.HTTPStatusError(
                f"retryable status {response.status_code} for {url}",
                request=response.request,
                response=response,
            )
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                delay = 2.0 * (attempt + 1)
            self.sleeper(delay)
        assert last_error is not None
        raise last_error

    def fetch_body(self, cik: str) -> bytes:
        """Raw companyfacts bytes for a CIK (used for byte-for-byte fixtures)."""
        return self._get(xbrl_url(cik)).content

    def company_facts(self, cik: str) -> dict[str, Any]:
        """Parsed companyfacts JSON for a CIK."""
        return json.loads(self.fetch_body(cik).decode("utf-8"))


def fixture_path(cik: str) -> str:
    """Repo-relative fixture filename for a CIK (recorded ``XBRL_LIVE=1``)."""
    return f"tests/fixtures/xbrl/CIK{pad_cik(cik)}.json"
