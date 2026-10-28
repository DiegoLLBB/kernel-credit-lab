"""Offline newsroom client tests (fixtures under tests/fixtures/newsroom/)."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.models import Filing, Issuer
from app.sources.newsroom import (
    FORM_PRESS_RELEASE,
    NewsroomClient,
    NewsroomConfig,
    NewsroomError,
    load_newsrooms,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "newsroom"
SEED_YAML = Path(__file__).resolve().parents[1] / "seed" / "newsrooms.yaml"

live = pytest.mark.skipif(
    os.getenv("NEWSROOM_LIVE") != "1",
    reason="set NEWSROOM_LIVE=1 to hit verizon.com (not used in CI)",
)

TENDER_LAUNCH = (
    "https://www.verizon.com/about/news/feed/verizon-debt-tender-offer-consent-solicitation"
)
TENDER_RESULTS = "https://www.verizon.com/about/node/929969"
REDEMPTION = "https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026"
RSS = "https://www.verizon.com/about/rss/news/financial-news.xml"
ROBOTS = "https://www.verizon.com/robots.txt"
LISTING = "https://www.verizon.com/about/news-category/financial"


def _settings() -> Settings:
    return Settings(sec_user_agent="Kernel Credit Lab tests@kernel.example.edu")


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _mock_transport() -> httpx.MockTransport:
    mapping = {
        ROBOTS: "verizon_robots.txt",
        RSS: "verizon_financial_news.xml",
        TENDER_LAUNCH: "verizon_tender_launch_2026-05-11.html",
        TENDER_RESULTS: "verizon_tender_results_2026-06-17.html",
        REDEMPTION: "verizon_redemption_2026-08-20.html",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers.get("user-agent")
        name = mapping.get(url)
        if name is None:
            return httpx.Response(404, text=f"missing fixture for {url}")
        body = _fixture_bytes(name)
        if name.endswith(".xml"):
            ctype = "application/rss+xml"
        elif name.endswith(".txt"):
            ctype = "text/plain"
        else:
            ctype = "text/html"
        return httpx.Response(200, content=body, headers={"content-type": ctype})

    return httpx.MockTransport(handler)


@pytest.fixture()
def newsroom() -> NewsroomClient:
    configs = {
        "verizon": NewsroomConfig(listing_url=LISTING, rss_url=RSS),
    }
    client = NewsroomClient(
        settings=_settings(),
        newsrooms=configs,
        transport=_mock_transport(),
        sleeper=lambda _s: None,
    )
    yield client
    client.close()


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_seed_yaml_configures_verizon_with_financial_rss() -> None:
    rooms = load_newsrooms(SEED_YAML)
    assert "verizon" in rooms
    assert rooms["verizon"].rss_url == RSS
    assert "financial" in rooms["verizon"].listing_url
    assert "duke" not in rooms


def test_list_releases_from_rss_includes_aug_redemption(newsroom: NewsroomClient) -> None:
    releases = newsroom.list_releases("verizon", since=date(2026, 8, 1), until=date(2026, 8, 31))
    by_url = {r.url: r for r in releases}
    assert REDEMPTION in by_url
    ref = by_url[REDEMPTION]
    assert ref.published == date(2026, 8, 20)
    assert "redeem" in ref.title.lower()
    assert "4.329%" in ref.title or "debt" in ref.title.lower()


def test_list_releases_refuses_unconfigured_issuer(newsroom: NewsroomClient) -> None:
    with pytest.raises(NewsroomError, match="no newsroom configured"):
        newsroom.list_releases("duke", since=date(2026, 1, 1), until=date(2026, 12, 31))


def test_fetch_text_tender_results_2100_row(newsroom: NewsroomClient) -> None:
    text = newsroom.fetch_text(TENDER_RESULTS)
    matches = [
        line
        for line in text.splitlines()
        if "2.100% Notes due 2028" in line and "1,142,981,000" in line
    ]
    assert matches, "expected 2.100% 2028 tendered amount on one table line"


def test_fetch_text_redemption_mentions_4329_and_sep_21(newsroom: NewsroomClient) -> None:
    text = newsroom.fetch_text(REDEMPTION)
    assert "4.329%" in text
    assert "2028" in text
    assert "September 21, 2026" in text


def test_upsert_releases_idempotent_on_url(newsroom: NewsroomClient, session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712", ticker="VZ")
    session.add(issuer)
    session.flush()
    releases = newsroom.list_releases("verizon", since=date(2026, 8, 20), until=date(2026, 8, 20))
    assert releases
    first = newsroom.upsert_releases(session, issuer, releases)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Filing)) == len(first)
    row = session.scalars(select(Filing).where(Filing.url == REDEMPTION)).one()
    assert row.form_type == FORM_PRESS_RELEASE
    assert row.accession_number is None
    assert row.content_hash is None
    assert row.filed_at == date(2026, 8, 20)

    second = newsroom.upsert_releases(session, issuer, releases)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Filing)) == len(first)
    assert {r.url for r in second} == {r.url for r in first}
    stored = session.scalars(select(Filing).where(Filing.url == REDEMPTION)).one()
    assert stored.content_hash is None


def test_fetch_release_text_sets_content_hash(newsroom: NewsroomClient, session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.flush()
    refs = newsroom.list_releases("verizon", since=date(2026, 8, 20), until=date(2026, 8, 20))
    rows = newsroom.upsert_releases(session, issuer, refs)
    session.commit()
    filing = next(r for r in rows if r.url == REDEMPTION)
    text = newsroom.fetch_release_text(session, filing)
    session.commit()
    assert "4.329%" in text
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert filing.content_hash == expected


def test_robots_disallow_refuses_path() -> None:
    """A path Disallow'd in the recorded robots.txt must not be fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == ROBOTS:
            return httpx.Response(
                200,
                content=_fixture_bytes("verizon_robots.txt"),
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="should not fetch")

    with (
        NewsroomClient(
            settings=_settings(),
            newsrooms={"verizon": NewsroomConfig(listing_url=LISTING, rss_url=RSS)},
            transport=httpx.MockTransport(handler),
            sleeper=lambda _s: None,
        ) as client,
        pytest.raises(NewsroomError, match="robots.txt disallows"),
    ):
        client.fetch_text("https://www.verizon.com/search/foo")


@live
def test_live_financial_rss_has_items() -> None:
    from app.config import get_settings
    from app.sources.newsroom import load_newsrooms

    get_settings.cache_clear()
    settings = get_settings()
    rooms = load_newsrooms()
    with NewsroomClient(settings=settings, newsrooms=rooms) as client:
        releases = client.list_releases("verizon", since=date(2026, 1, 1), until=date(2026, 12, 31))
    assert releases
