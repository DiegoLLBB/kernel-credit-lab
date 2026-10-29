"""FRED client for Treasury yields and FX used by the refinancing engine.

Supported series
----------------
Treasury constant-maturity yields (percent): ``DGS1``, ``DGS2``, ``DGS3``, ``DGS5``, ``DGS10``.

FX (foreign-exchange rates):
- ``DEXUSEU`` — USD per EUR
- ``DEXUSUK`` — USD per GBP
- ``DEXCAUS`` — **CAD per USD** (note the inverted quotation vs the other two)

API key from ``FRED_API_KEY`` (never hardcoded). The key is redacted from error messages
and from any URL we persist. Rate-limited and retried like ``EdgarClient``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import RateObservation

FRED_API = "https://api.stlouisfed.org/fred"
MAX_RPS = 2.0  # FRED allows 120 req/min; stay well under

TREASURY_SERIES = ("DGS1", "DGS2", "DGS3", "DGS5", "DGS10")
FX_SERIES = ("DEXUSEU", "DEXUSUK", "DEXCAUS")
SUPPORTED_SERIES = TREASURY_SERIES + FX_SERIES

_API_KEY_RE = re.compile(r"(api_key=)[^&]+", re.I)


class FredError(RuntimeError):
    """FRED request or configuration failure."""


@dataclass(frozen=True)
class Observation:
    obs_date: date
    value: Decimal | None


def redact_api_key(text: str) -> str:
    """Strip any api_key query value from a URL or error string."""
    return _API_KEY_RE.sub(r"\1REDACTED", text)


def _require_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise FredError("FRED_API_KEY is required")
    return key


def parse_observation_value(raw: str | None) -> Decimal | None:
    """FRED uses ``.`` for a missing observation."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in {"", "."}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise FredError(f"invalid FRED observation value: {text!r}") from exc


def parse_observations_payload(payload: dict[str, Any]) -> list[Observation]:
    rows: list[Observation] = []
    for item in payload.get("observations") or []:
        date_s = item.get("date")
        if not date_s:
            continue
        rows.append(
            Observation(
                obs_date=date.fromisoformat(str(date_s)),
                value=parse_observation_value(item.get("value")),
            )
        )
    return rows


class FredClient:
    """Synchronous FRED client with rate limiting, retries, and key redaction."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ) -> None:
        self.settings = settings or get_settings()
        self._api_key = _require_api_key(self.settings.fred_api_key)
        self._clock = clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._min_interval = 1.0 / MAX_RPS
        self._last_request_at = 0.0
        self._owns_client = client is None
        self._http = client or httpx.Client(
            transport=transport,
            timeout=60.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> FredClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        now = self._clock()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            self._sleeper(wait)

    def _observations_url(
        self,
        series_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        sort_order: str = "asc",
        limit: int | None = None,
    ) -> str:
        params: dict[str, str] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": sort_order,
        }
        if start is not None:
            params["observation_start"] = start.isoformat()
        if end is not None:
            params["observation_end"] = end.isoformat()
        if limit is not None:
            params["limit"] = str(limit)
        return f"{FRED_API}/series/observations?{urlencode(params)}"

    def _get(self, url: str) -> httpx.Response:
        safe = redact_api_key(url)
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            self._last_request_at = self._clock()
            try:
                resp = self._http.get(url)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise FredError(f"FRED transport error for {safe}: {exc}") from exc
                self._sleeper(min(2**attempt, 30))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self._max_retries:
                    raise FredError(f"FRED {resp.status_code} for {safe} after {attempt} attempts")
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                self._sleeper(min(delay, 60))
                continue

            if resp.status_code >= 400:
                body = redact_api_key(resp.text[:200])
                raise FredError(f"FRED {resp.status_code} for {safe}: {body}")
            return resp

    def series_observations(
        self, series_id: str, start: date, end: date
    ) -> list[tuple[date, Decimal | None]]:
        """Return ``(date, value)`` for ``series_id`` in ``[start, end]`` (inclusive).

        FRED's ``.`` values become ``None``.
        """
        if start > end:
            raise ValueError("start must be ≤ end")
        series_id = series_id.strip().upper()
        url = self._observations_url(series_id, start=start, end=end, sort_order="asc")
        payload = self._get(url).json()
        return [(obs.obs_date, obs.value) for obs in parse_observations_payload(payload)]

    def latest_on_or_before(self, series_id: str, d: date) -> tuple[date, Decimal] | None:
        """Last non-missing observation with ``obs_date ≤ d`` (never uses a later date)."""
        series_id = series_id.strip().upper()
        # Ask FRED for history ending on d, newest first; skip missing "." rows.
        url = self._observations_url(series_id, end=d, sort_order="desc", limit=30)
        payload = self._get(url).json()
        for obs in parse_observations_payload(payload):
            if obs.obs_date > d:
                continue  # defensive; API should already bound by observation_end
            if obs.value is None:
                continue
            return obs.obs_date, obs.value
        return None

    def upsert_observations(
        self,
        session: Session,
        series_id: str,
        observations: list[tuple[date, Decimal | None]],
    ) -> list[RateObservation]:
        """Insert or update ``RateObservation`` rows; idempotent on (series_id, obs_date)."""
        series_id = series_id.strip().upper()
        result: list[RateObservation] = []
        for obs_date, value in observations:
            row = session.scalars(
                select(RateObservation).where(
                    RateObservation.series_id == series_id,
                    RateObservation.obs_date == obs_date,
                )
            ).first()
            if row is None:
                row = RateObservation(series_id=series_id, obs_date=obs_date)
                session.add(row)
            row.value = value
            result.append(row)
        session.flush()
        return result
