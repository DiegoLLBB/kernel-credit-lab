"""Ticker onboard: resolve, missing tags, idempotent re-onboard, admin auth."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base, get_session
from app.main import app
from app.models import Issuer, MaturitySchedule, Watchlist
from app.onboard.service import onboard_ticker
from app.onboard.sic import sector_from_sic
from app.onboard.tags import build_xbrl_config
from app.onboard.tickers import TickerClient
from app.sources.edgar import pad_cik

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "xbrl"


def _transport_with_tickers(tickers_doc: dict, facts_by_cik: dict[str, dict] | None = None):
    facts_by_cik = facts_by_cik or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=tickers_doc)
        if "/submissions/CIK" in url:
            cik = url.rsplit("CIK", 1)[-1].removesuffix(".json")
            return httpx.Response(
                200,
                json={"cik": cik, "sic": "7372", "sicDescription": "Services-Prepackaged Software"},
            )
        if "/companyfacts/CIK" in url:
            cik = url.rsplit("CIK", 1)[-1].removesuffix(".json")
            bare = cik.lstrip("0") or "0"
            for key, doc in facts_by_cik.items():
                if pad_cik(key) == pad_cik(cik) or key.lstrip("0") == bare:
                    return httpx.Response(200, json=doc)
            return httpx.Response(404, text="missing")
        return httpx.Response(404, text=url)

    return httpx.MockTransport(handler)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_sector_from_sic():
    assert sector_from_sic(7372, "Services-Prepackaged Software") == "Information Technology"
    assert sector_from_sic(4813, "Telephone Communications") == "Communication Services"
    assert sector_from_sic(4911, "Electric Services") == "Utilities"
    assert sector_from_sic(3721, "Aircraft") == "Industrials"


def test_resolve_sector_gics_map():
    from app.onboard.sectors import resolve_sector

    sector, src = resolve_sector("PG")
    assert sector == "Consumer Staples"
    assert src == "gics_map"
    sector, src = resolve_sector("XOM")
    assert sector == "Energy"
    sector, src = resolve_sector("ZZZZNOPE", 4911, "Electric Services")
    assert sector == "Utilities (SIC)"
    assert src == "sic"


def test_resolve_ticker():
    tickers = {
        "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
        "1": {"cik_str": 732712, "ticker": "VZ", "title": "VERIZON COMMUNICATIONS INC"},
    }
    client = TickerClient(
        user_agent="Kernel Credit Lab test@kernelterminal.com",
        transport=_transport_with_tickers(tickers),
    )
    match = client.resolve("msft")
    assert match.ticker == "MSFT"
    assert match.cik == "0000789019"
    assert "MICROSOFT" in match.name.upper()
    with pytest.raises(KeyError):
        client.resolve("NOPE")


def test_resolve_xom_override():
    # Override must win even when company_tickers maps XOM to a thin HoldCo.
    tickers = {
        "0": {
            "cik_str": 2115436,
            "ticker": "XOM",
            "title": "ExxonMobil Holdings Corp",
        },
    }
    client = TickerClient(
        user_agent="Kernel Credit Lab test@kernelterminal.com",
        transport=_transport_with_tickers(tickers),
    )
    match = client.resolve("xom")
    assert match.cik == "0000034088"
    assert match.name == "Exxon Mobil Corporation"


def test_build_xbrl_config_missing_debt_refused():
    doc = {"cik": 1, "entityName": "Empty Co", "facts": {"us-gaap": {}}}
    with pytest.raises(ValueError, match="no us-gaap"):
        build_xbrl_config(doc, cik="0000000001")

    # Cash only — no debt tags.
    doc = {
        "cik": 1,
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 100,
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-01",
                                "accn": "0001",
                            }
                        ]
                    }
                }
            }
        },
    }
    with pytest.raises(ValueError, match="No usable XBRL debt"):
        build_xbrl_config(doc, cik="0000000001", as_of=date(2026, 6, 1))


@pytest.mark.asyncio
async def test_onboard_idempotent_with_fixture(db_session: Session):
    # Use a recorded seed fixture (Verizon) so debt + maturity tags exist.
    path = FIXTURE_DIR / "CIK0000732712.json"
    if not path.is_file():
        pytest.skip("verizon companyfacts fixture missing")
    facts = json.loads(path.read_text())
    tickers = {
        "0": {"cik_str": 732712, "ticker": "VZ", "title": "VERIZON COMMUNICATIONS INC"},
    }
    transport = _transport_with_tickers(tickers, {"732712": facts})
    settings = Settings(
        sec_user_agent="Kernel Credit Lab test@kernelterminal.com",
        admin_api_token="secret-token",
        model_api_key="",
    )
    from app.onboard.tickers import TickerClient
    from app.sources.xbrl import XbrlClient

    tickers_client = TickerClient(user_agent=settings.sec_user_agent, transport=transport)
    xbrl_client = XbrlClient(user_agent=settings.sec_user_agent, transport=transport)

    first = await onboard_ticker(
        db_session,
        "VZ",
        settings=settings,
        tickers=tickers_client,
        xbrl=xbrl_client,
        as_of=date(2026, 6, 1),
        generate_insight=False,
    )
    db_session.commit()
    assert first.created is True
    assert first.issuer_key == "vz"
    assert first.wall_buckets >= 1
    issuers = db_session.scalars(select(Issuer)).all()
    assert len(issuers) == 1
    wl = db_session.scalars(select(Watchlist)).one()
    assert wl.monitor_from == date(2026, 6, 1)
    walls = db_session.scalars(select(MaturitySchedule)).all()
    assert walls
    assert any(
        (b.get("note") == "rest of the debt reported in the 10-K")
        for w in walls
        for b in (w.buckets or [])
    )

    second = await onboard_ticker(
        db_session,
        "VZ",
        settings=settings,
        tickers=tickers_client,
        xbrl=xbrl_client,
        as_of=date(2026, 6, 1),
        generate_insight=False,
    )
    db_session.commit()
    assert second.created is False
    assert len(db_session.scalars(select(Issuer)).all()) == 1


def test_post_issuers_auth(db_session: Session):
    engine = db_session.get_bind()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_db():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    from app.config import get_settings

    app.dependency_overrides[get_session] = _override_db

    def _no_token():
        return Settings(admin_api_token="", sec_user_agent="Kernel Credit Lab t@t.com")

    app.dependency_overrides[get_settings] = _no_token
    with TestClient(app) as client:
        r = client.post("/api/issuers", json={"ticker": "MSFT"})
        assert r.status_code == 503

    def _with_token():
        return Settings(
            admin_api_token="correct-token",
            sec_user_agent="Kernel Credit Lab t@t.com",
        )

    app.dependency_overrides[get_settings] = _with_token
    with TestClient(app) as client:
        r = client.post(
            "/api/issuers",
            json={"ticker": "MSFT"},
            headers={"X-Admin-Token": "wrong"},
        )
        assert r.status_code == 401
        r = client.post("/api/issuers", json={"ticker": "MSFT"})
        assert r.status_code == 401
        r = client.post(
            "/api/issuers",
            json={"ticker": "MSFT"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401
    app.dependency_overrides.clear()
