"""Peer median/rank and metric-alert band / due_12m rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.metrics.metric_alerts import _dedupe_key, scan_metric_alerts
from app.metrics.peers import BANK_NA_REASON, is_bank_sector, peer_comparison, watchlist_rank
from app.models import Alert, CreditMetric, Issuer


@pytest.fixture()
def db_session() -> Session:
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


def test_is_bank_sector() -> None:
    assert is_bank_sector("Financials")
    assert is_bank_sector("Banks")
    assert not is_bank_sector("Utilities")


def test_peer_comparison_under_three(db_session: Session) -> None:
    a = Issuer(key="a", name="A", sector="Utilities", ticker="A")
    b = Issuer(key="b", name="B", sector="Utilities", ticker="B")
    db_session.add_all([a, b])
    db_session.flush()
    for iss, val in ((a, "5.2"), (b, "5.6")):
        db_session.add(
            CreditMetric(
                issuer_id=iss.id,
                metric="leverage",
                period_end=date(2025, 12, 31),
                fiscal_label="FY2025",
                filed=date(2026, 2, 1),
                value=Decimal(val),
                unit="x",
            )
        )
    db_session.flush()
    raw = peer_comparison(db_session, a, "leverage", date(2026, 11, 1), value=Decimal("5.2"))
    assert raw is not None
    assert raw["n"] == 2
    assert raw["median"] is None
    assert "fewer than 3" in (raw["note"] or "")


def test_peer_comparison_rank(db_session: Session) -> None:
    peers = []
    for i, val in enumerate(("4.0", "5.0", "6.0")):
        iss = Issuer(key=f"u{i}", name=f"U{i}", sector="Utilities", ticker=f"U{i}")
        db_session.add(iss)
        peers.append((iss, val))
    db_session.flush()
    for iss, val in peers:
        db_session.add(
            CreditMetric(
                issuer_id=iss.id,
                metric="leverage",
                period_end=date(2025, 12, 31),
                fiscal_label="FY2025",
                filed=date(2026, 2, 1),
                value=Decimal(val),
                unit="x",
            )
        )
    db_session.flush()
    mid = peers[1][0]
    raw = peer_comparison(db_session, mid, "leverage", date(2026, 11, 1), value=Decimal("5.0"))
    assert raw is not None
    assert raw["n"] == 3
    assert raw["rank"] == 2
    assert raw["ordinal"] == "2nd"
    assert float(raw["median"]) == 5.0


def test_watchlist_rank_always_works_with_two(db_session: Session) -> None:
    a = Issuer(key="a", name="A", sector="Utilities", ticker="A")
    b = Issuer(key="b", name="B", sector="Technology", ticker="B")
    db_session.add_all([a, b])
    db_session.flush()
    for iss, val in ((a, "3.0"), (b, "5.0")):
        db_session.add(
            CreditMetric(
                issuer_id=iss.id,
                metric="leverage",
                period_end=date(2025, 12, 31),
                fiscal_label="FY2025",
                filed=date(2026, 2, 1),
                value=Decimal(val),
                unit="x",
            )
        )
    db_session.flush()
    # Sector peer median unavailable (n<3), but watchlist rank still works.
    sector = peer_comparison(db_session, a, "leverage", date(2026, 11, 1), value=Decimal("3.0"))
    assert sector is not None and sector["median"] is None
    wl = watchlist_rank(db_session, a, "leverage", date(2026, 11, 1), value=Decimal("3.0"))
    assert wl is not None
    assert wl["n"] == 2
    assert wl["rank"] == 1
    assert "lowest of 2" in wl["line"]


def test_metric_alert_band_cross(db_session: Session) -> None:
    iss = Issuer(key="mfg", name="Mfg Co", sector="Industrials", ticker="MFG")
    db_session.add(iss)
    db_session.flush()
    # 1.5× is green; 5.5× is red under default leverage bands.
    db_session.add(
        CreditMetric(
            issuer_id=iss.id,
            metric="leverage",
            period_end=date(2024, 12, 31),
            fiscal_label="FY2024",
            filed=date(2025, 2, 15),
            value=Decimal("1.5"),
            unit="x",
        )
    )
    db_session.add(
        CreditMetric(
            issuer_id=iss.id,
            metric="leverage",
            period_end=date(2025, 12, 31),
            fiscal_label="FY2025",
            filed=date(2026, 2, 15),
            value=Decimal("5.5"),
            unit="x",
            accession="0000000000-26-000001",
        )
    )
    db_session.flush()
    stats = scan_metric_alerts(db_session, as_of=date(2026, 11, 1), issuer_keys=["mfg"])
    assert stats["created"] >= 1
    alert = db_session.scalars(select(Alert).where(Alert.kind == "metric")).first()
    assert alert is not None
    assert alert.kind == "metric"
    assert "→" in alert.title
    key = _dedupe_key(iss.id, "leverage", date(2025, 12, 31), "band_cross")
    assert key in (alert.materiality_reasons or [])
    # Idempotent
    stats2 = scan_metric_alerts(db_session, as_of=date(2026, 11, 1), issuer_keys=["mfg"])
    assert stats2["created"] == 0
    assert stats2["skipped"] >= 1


def test_bank_na_reason_constant() -> None:
    assert BANK_NA_REASON == "not meaningful for banks"
