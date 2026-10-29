"""Offline FRED client tests (fixtures under tests/fixtures/fred/)."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.models import RateObservation
from app.sources.fred import (
    FredClient,
    FredError,
    parse_observation_value,
    redact_api_key,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fred"

live = pytest.mark.skipif(
    os.getenv("FRED_LIVE") != "1",
    reason="set FRED_LIVE=1 (and FRED_API_KEY) to hit api.stlouisfed.org",
)

DGS5_FIXTURE = "DGS5_2026-01-01_2026-10-31.json"
DEXUSEU_FIXTURE = "DEXUSEU_2026-01-01_2026-10-31.json"


def _settings() -> Settings:
    return Settings(fred_api_key="test-fred-key-not-for-production")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "api_key=" in url
        # Callers must never see the key leak into assertions via this handler's logs;
        # ensure query parses and series is routed to a fixture.
        qs = parse_qs(urlparse(url).query)
        series = qs.get("series_id", [""])[0]
        sort = qs.get("sort_order", ["asc"])[0]
        if series == "DGS5":
            payload = _fixture(DGS5_FIXTURE)
        elif series == "DEXUSEU":
            payload = _fixture(DEXUSEU_FIXTURE)
        else:
            return httpx.Response(404, text=f"no fixture for {series}")

        obs = list(payload["observations"])
        start = qs.get("observation_start", [None])[0]
        end = qs.get("observation_end", [None])[0]
        if start:
            obs = [o for o in obs if o["date"] >= start]
        if end:
            obs = [o for o in obs if o["date"] <= end]
        reverse = sort.lower() == "desc"
        obs.sort(key=lambda o: o["date"], reverse=reverse)
        limit = qs.get("limit", [None])[0]
        if limit:
            obs = obs[: int(limit)]
        body = {**payload, "observations": obs, "count": len(obs), "sort_order": sort}
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


@pytest.fixture()
def fred() -> FredClient:
    client = FredClient(settings=_settings(), transport=_mock_transport(), sleeper=lambda _s: None)
    yield client
    client.close()


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_requires_api_key() -> None:
    with pytest.raises(FredError, match="FRED_API_KEY"):
        FredClient(settings=Settings(fred_api_key=""))


def test_redact_api_key() -> None:
    url = "https://api.stlouisfed.org/fred/series/observations?series_id=DGS5&api_key=SECRET123&file_type=json"
    assert "SECRET123" not in redact_api_key(url)
    assert "api_key=REDACTED" in redact_api_key(url)


def test_parse_dot_as_missing() -> None:
    assert parse_observation_value(".") is None
    assert parse_observation_value("") is None
    assert parse_observation_value("4.18") == Decimal("4.18")


def test_series_observations_dgs5(fred: FredClient) -> None:
    rows = fred.series_observations("DGS5", date(2026, 1, 1), date(2026, 10, 31))
    assert rows
    assert rows[0][0] == date(2026, 1, 2)
    assert rows[0][1] == Decimal("3.74")
    # Holiday row is present as None (FRED ".").
    by_date = dict(rows)
    assert date(2026, 1, 19) in by_date
    assert by_date[date(2026, 1, 19)] is None
    assert by_date[date(2026, 6, 15)] == Decimal("4.18")


def test_series_observations_dexuseu(fred: FredClient) -> None:
    rows = fred.series_observations("DEXUSEU", date(2026, 1, 1), date(2026, 10, 31))
    assert rows[0][1] == Decimal("1.1738")
    by_date = dict(rows)
    assert by_date[date(2026, 6, 15)] == Decimal("1.1599")


def test_latest_on_or_before_skips_weekend_and_never_uses_future(fred: FredClient) -> None:
    # Sunday 14 Jun 2026 → Friday 12 Jun 4.21 (not Monday 15 Jun).
    got = fred.latest_on_or_before("DGS5", date(2026, 6, 14))
    assert got == (date(2026, 6, 12), Decimal("4.21"))
    on_monday = fred.latest_on_or_before("DGS5", date(2026, 6, 15))
    assert on_monday == (date(2026, 6, 15), Decimal("4.18"))


def test_latest_on_or_before_skips_missing_dot(fred: FredClient) -> None:
    # 19 Jan 2026 is a holiday with value "."; prior observation is 16 Jan 3.82.
    got = fred.latest_on_or_before("DGS5", date(2026, 1, 19))
    assert got == (date(2026, 1, 16), Decimal("3.82"))


def test_upsert_observations_idempotent(fred: FredClient, session: Session) -> None:
    rows = fred.series_observations("DGS5", date(2026, 6, 15), date(2026, 6, 15))
    first = fred.upsert_observations(session, "DGS5", rows)
    session.commit()
    assert session.scalar(select(func.count()).select_from(RateObservation)) == len(first)
    second = fred.upsert_observations(session, "DGS5", rows)
    session.commit()
    assert session.scalar(select(func.count()).select_from(RateObservation)) == len(first)
    assert {r.obs_date for r in second} == {r.obs_date for r in first}
    stored = session.scalars(
        select(RateObservation).where(
            RateObservation.series_id == "DGS5",
            RateObservation.obs_date == date(2026, 6, 15),
        )
    ).one()
    assert stored.value == Decimal("4.18")


def test_error_message_redacts_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad api_key=should-not-leak-in-full")

    with (
        FredClient(
            settings=_settings(),
            transport=httpx.MockTransport(handler),
            sleeper=lambda _s: None,
        ) as client,
        pytest.raises(FredError) as excinfo,
    ):
        client.series_observations("DGS5", date(2026, 1, 1), date(2026, 1, 2))
    msg = str(excinfo.value)
    assert "test-fred-key-not-for-production" not in msg
    assert "api_key=REDACTED" in msg


@live
def test_live_dgs5_jan_2026() -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.fred_api_key:
        pytest.skip("FRED_API_KEY not configured")
    with FredClient(settings=settings) as client:
        rows = client.series_observations("DGS5", date(2026, 1, 2), date(2026, 1, 2))
    assert rows and rows[0][1] == Decimal("3.74")
