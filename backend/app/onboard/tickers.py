"""Resolve a ticker to CIK + name via SEC company_tickers.json."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.sources.edgar import _require_user_agent, pad_cik

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
MAX_RETRIES = 5
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}

# SEC company_tickers.json sometimes remaps a household ticker to a thin
# holding-company CIK. Prefer the operating issuer we can actually load.
TICKER_CIK_OVERRIDES: dict[str, tuple[int, str]] = {
    # XOM currently points at ExxonMobil Holdings Corp (2115436) with sparse
    # XBRL; Exxon Mobil Corporation remains CIK 34088.
    "XOM": (34088, "Exxon Mobil Corporation"),
}


@dataclass(frozen=True)
class TickerMatch:
    ticker: str
    cik: str
    name: str
    cik_int: int


@dataclass
class TickerClient:
    """Polite SEC ticker / submissions fetcher (same UA + rate limits as EDGAR)."""

    user_agent: str
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    max_retries: int = MAX_RETRIES
    min_interval: float = 0.12
    _last_call: float = field(default=0.0, init=False, repr=False)
    _tickers_cache: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def _throttle(self) -> None:
        now = self.clock()
        wait = self.min_interval - (now - self._last_call)
        if wait > 0:
            self.sleeper(wait)
            now = self.clock()
        self._last_call = now

    def _get_json(self, url: str) -> Any:
        ua = _require_user_agent(self.user_agent)
        headers = {"User-Agent": ua, "Accept": "application/json"}
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
                return response.json()
            if response.status_code not in RETRYABLE:
                response.raise_for_status()
            last_error = httpx.HTTPStatusError(
                f"HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                delay = 2.0 * (attempt + 1)
            self.sleeper(delay)
        raise RuntimeError(f"SEC request failed for {url}: {last_error}")

    def company_tickers(self) -> dict[str, Any]:
        if self._tickers_cache is None:
            self._tickers_cache = self._get_json(TICKERS_URL)
        return self._tickers_cache or {}

    def resolve(self, ticker: str) -> TickerMatch:
        wanted = (ticker or "").strip().upper()
        if not wanted:
            raise ValueError("ticker is required")
        override = TICKER_CIK_OVERRIDES.get(wanted)
        if override is not None:
            cik_int, name = override
            return TickerMatch(
                ticker=wanted,
                cik=pad_cik(cik_int),
                name=name,
                cik_int=cik_int,
            )
        doc = self.company_tickers()
        for row in doc.values():
            if not isinstance(row, dict):
                continue
            if str(row.get("ticker") or "").upper() != wanted:
                continue
            cik_int = int(row["cik_str"])
            return TickerMatch(
                ticker=wanted,
                cik=pad_cik(cik_int),
                name=str(row.get("title") or wanted),
                cik_int=cik_int,
            )
        raise KeyError(f"ticker {wanted!r} not found in SEC company_tickers.json")

    def submissions(self, cik: str) -> dict[str, Any]:
        return self._get_json(SUBMISSIONS_URL.format(cik=pad_cik(cik)))
