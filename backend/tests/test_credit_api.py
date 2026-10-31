"""Analyst-layer read API: overview, issuer snapshot, alert impact sentences."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import Alert, Issuer
from app.models.credit import CreditInsight, CreditMetric
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"


@pytest.fixture()
def api_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def api_db(api_engine) -> Session:
    factory = sessionmaker(bind=api_engine, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(api_engine):
    factory = sessionmaker(bind=api_engine, expire_on_commit=False)

    def _override():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_credit(session: Session) -> Alert:
    load_seed(session, SEED_PATH)
    session.commit()
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    rows = [
        ("total_debt", date(2024, 12, 31), "FY2024", Decimal("188618"), date(2025, 2, 7)),
        ("total_debt", date(2025, 12, 31), "FY2025", Decimal("179352"), date(2026, 2, 6)),
        ("leverage", date(2024, 12, 31), "FY2024", Decimal("4.1"), date(2025, 2, 7)),
        ("leverage", date(2025, 12, 31), "FY2025", Decimal("3.8"), date(2026, 2, 6)),
        (
            "fcf_to_debt",
            date(2025, 12, 31),
            "FY2025",
            None,
            date(2026, 2, 6),
        ),
    ]
    for metric, end, label, value, filed in rows:
        detail = (
            None
            if value is not None
            else {"missing": "Capital expenditure is not tagged by this filer"}
        )
        session.add(
            CreditMetric(
                issuer_id=verizon.id,
                metric=metric,
                period_end=end,
                fiscal_label=label,
                value=value,
                unit="USD",
                filed=filed,
                detail=detail,
            )
        )
    session.add(
        CreditInsight(
            issuer_id=verizon.id,
            as_of=date(2026, 6, 30),
            scope="snapshot",
            ref="",
            input_hash="h" * 64,
            headline="Verizon: refinancing risk falling",
            sentences=["Total debt fell to $179.4 billion in 2025."],
            dropped=[],
            status="complete",
        )
    )
    alert = Alert(
        issuer_id=verizon.id,
        title="Verizon redeems notes",
        summary="Redemption",
        status="new",
    )
    session.add(alert)
    session.flush()
    session.add(
        CreditInsight(
            issuer_id=verizon.id,
            as_of=date(2026, 8, 20),
            scope="impact",
            ref=alert.id,
            input_hash="i" * 64,
            headline=None,
            sentences=["Redemption cut 2027 maturities by 12%."],
            dropped=[],
            status="complete",
        )
    )
    session.commit()
    return alert


def test_overview_shape(client: TestClient, api_db: Session) -> None:
    _seed_credit(api_db)
    resp = client.get("/api/credit/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["header"]["companies"] >= 6
    assert body["header"]["material_changes"] >= 1
    feed_item = next(i for i in body["feed"] if i["title"] == "Verizon redeems notes")
    assert feed_item["impact_sentence"] == "Redemption cut 2027 maturities by 12%."
    assert feed_item["impact_pending"] is False
    assert any(
        row["issuer"]["key"] == "verizon"
        and any(t["metric"] == "total_debt" and t["value"] is not None for t in row["metrics"])
        for row in body["watchlist"]
    )
    vz = next(row for row in body["watchlist"] if row["issuer"]["key"] == "verizon")
    debt_tile = next(t for t in vz["metrics"] if t["metric"] == "total_debt")
    assert debt_tile["change_pct"] is not None
    assert debt_tile["flag"] in ("green", "amber", "red", "none", None)
    assert isinstance(body["thresholds"], list) and body["thresholds"]
    assert body["evaluation"] is None or "Tested on" in body["evaluation"]["text"]


def test_snapshot_shape(client: TestClient, api_db: Session) -> None:
    _seed_credit(api_db)
    resp = client.get("/api/issuers/verizon/snapshot?as_of=2026-06-30")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["insight"]["status"] == "complete"
    assert body["insight"]["headline"] == "Verizon: refinancing risk falling"
    assert body["insight"]["sentences"] == ["Total debt fell to $179.4 billion in 2025."]
    assert body["insight_pending"] is False
    assert any(t["metric"] == "total_debt" for t in body["metrics"])
    missing = next(t for t in body["metrics"] if t["metric"] == "fcf_to_debt")
    assert missing["value"] is None
    assert "not tagged" in (missing["missing_reason"] or "")
    assert "wall" in body
    assert isinstance(body["timeline"], list)


def test_snapshot_pending_without_insight(client: TestClient, api_db: Session) -> None:
    load_seed(api_db, SEED_PATH)
    api_db.commit()
    resp = client.get("/api/issuers/verizon/snapshot")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["insight_pending"] is True
    assert body["insight"] is None


def test_alert_impact_fields(client: TestClient, api_db: Session) -> None:
    alert = _seed_credit(api_db)
    resp = client.get("/api/alerts")
    assert resp.status_code == 200, resp.text
    item = next(a for a in resp.json()["items"] if a["id"] == alert.id)
    assert item["impact_sentence"] == "Redemption cut 2027 maturities by 12%."
    assert item["impact_status"] == "complete"
    detail = client.get(f"/api/alerts/{alert.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["impact_sentence"] == "Redemption cut 2027 maturities by 12%."
