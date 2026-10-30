"""Dashboard read API: seed + fake-model run against in-memory SQLite (not shared Postgres)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.claims import reset_claims_client, use_claims_client
from app.agent.fake import FakeClaimsClient, FakeModel
from app.agent.loop import RunLimits, run_event
from app.agent.run import start_agent_run
from app.agent.text import html_to_text
from app.db import Base, get_session
from app.main import app
from app.models import (
    Alert,
    AuditEntry,
    Event,
    EventFiling,
    Filing,
    Instrument,
    Issuer,
    ReplayExpectation,
)
from app.models.trace import AgentRun
from scripts.load_seed import load_seed
from tests.test_agent_loop import _verizon_script
from tests.test_agent_tools import REDEMPTION_HTML, _happy_path

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"


@pytest.fixture()
def api_engine():
    # One shared in-memory DB across TestClient worker threads.
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


def _seed_verizon(session: Session) -> tuple[Event, Filing, Instrument]:
    load_seed(session, SEED_PATH)
    session.commit()
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    bond = session.scalars(select(Instrument).where(Instrument.key == "vz_4329_2028")).one()
    html = REDEMPTION_HTML.read_text(encoding="utf-8", errors="replace")
    text = html_to_text(html)
    filing = Filing(
        issuer_id=verizon.id,
        form_type="press_release",
        url="https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026",
        filed_at=date(2026, 8, 20),
        title="Verizon to redeem debt securities in September",
        body_text=text,
    )
    session.add(filing)
    session.flush()
    event = Event(
        replay_key="vz_redemption_api_test",
        event_type="press_release",
        issuer_id=verizon.id,
        event_date=date(2026, 8, 20),
        source_filing_id=filing.id,
        summary="Verizon redemption",
        status="pending",
    )
    session.add(event)
    session.flush()
    session.add(EventFiling(event_id=event.id, filing_id=filing.id))
    session.add(
        ReplayExpectation(
            key="vz_redemption_api_test",
            issuer_key="verizon",
            event_type="press_release",
            event_date=date(2026, 8, 20),
            available_from=date(2026, 8, 20),
            title="Verizon redemption",
            sources=[],
            confirming_sources=[],
            expected_changes=[],
            expected_alert=True,
            why="test",
            event_id=event.id,
        )
    )
    session.commit()
    return event, filing, bond


@pytest.fixture()
def seeded_alert(api_db: Session) -> dict:
    event, filing, _bond = _seed_verizon(api_db)
    out = _happy_path(api_db, event, filing)
    api_db.commit()
    return {"session": api_db, "event": event, "filing": filing, **out}


def test_cors_allows_firebase_and_localhost(client: TestClient) -> None:
    for origin in (
        "http://localhost:3000",
        "https://kernel-credit-lab.web.app",
        "https://credit.kernelterminal.com",
    ):
        resp = client.options(
            "/api/issuers",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code in {200, 204}
        assert resp.headers.get("access-control-allow-origin") == origin


def test_list_and_get_issuers(client: TestClient, api_db: Session) -> None:
    load_seed(api_db, SEED_PATH)
    api_db.commit()
    listed = client.get("/api/issuers")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 5
    keys = {i["key"] for i in body["items"]}
    assert "verizon" in keys
    assert "duke_corp" in keys

    detail = client.get("/api/issuers/verizon", params={"as_of": "2025-12-31"})
    assert detail.status_code == 200
    d = detail.json()
    assert d["key"] == "verizon"
    assert d["instruments"]
    assert "buckets" in d["maturity_wall"]
    dumped = json.dumps(d)
    assert "expected_alert" not in dumped
    assert '"why"' not in dumped


def test_alerts_list_detail_review_and_document(client: TestClient, seeded_alert: dict) -> None:
    run = seeded_alert["run"]
    filing = seeded_alert["filing"]
    session: Session = seeded_alert["session"]
    alert = session.scalars(select(Alert).where(Alert.run_id == run.id)).one()

    listed = client.get("/api/alerts", params={"issuer": "verizon", "status": "new"})
    assert listed.status_code == 200
    data = listed.json()
    assert data["total"] >= 1
    item = next(a for a in data["items"] if a["id"] == alert.id)
    assert item["material"] is True
    assert item["run_id"] == run.id
    assert item["issuer"]["key"] == "verizon"
    assert item["materiality_reasons"]

    detail = client.get(f"/api/alerts/{alert.id}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["summary"]
    assert d["findings"]
    assert d["findings"][0]["citations"]
    cite = d["findings"][0]["citations"][0]
    assert cite["exact_quote"]
    assert cite["document_url"]
    assert d["applied_changes"]
    assert d["affected_buckets"]
    assert d["claims_check"] is not None
    assert d["claims_check"]["status"] == "pass"

    fact_id = cite["id"]
    doc = client.get(
        f"/api/documents/{filing.id}",
        params={"page": cite.get("page") or 0, "fact_id": fact_id},
    )
    assert doc.status_code == 200
    page = doc.json()
    assert page["text"]
    assert page["highlight"] is not None
    assert page["highlight"]["fact_id"] == fact_id
    assert page["highlight"]["end"] > page["highlight"]["start"]

    review = client.post(
        f"/api/alerts/{alert.id}/review",
        json={"action": "confirm", "note": "Looks right"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "confirmed"
    session.refresh(alert)
    assert alert.status == "confirmed"
    audits = session.scalars(
        select(AuditEntry).where(
            AuditEntry.run_id == run.id,
            AuditEntry.actor == "analyst",
            AuditEntry.action == "alert_confirmed",
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].detail and audits[0].detail.get("note") == "Looks right"

    dismissed = client.post(
        f"/api/alerts/{alert.id}/review",
        json={"action": "dismiss", "note": "noise"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_run_trace_from_fake_model(client: TestClient, api_db: Session) -> None:
    event, filing, _bond = _seed_verizon(api_db)
    script = _verizon_script(filing.id)
    fake = FakeModel(script, cost_per_call=Decimal("0.001"))
    claims = FakeClaimsClient(
        [
            {
                "summary": {"status": "supported", "unsupported_phrase": None},
                "findings": [
                    {
                        "finding_index": i,
                        "item_key": None,
                        "status": "supported",
                        "unsupported_phrase": None,
                    }
                    for i in range(20)
                ],
            }
        ]
    )
    token = use_claims_client(claims)
    try:
        run = await run_event(
            api_db,
            event.id,
            fake,
            RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        )
    finally:
        reset_claims_client(token)
    api_db.commit()
    assert run.status == "alerted", (run.status, run.error, run.result)

    trace = client.get(f"/api/runs/{run.id}/trace")
    assert trace.status_code == 200
    body = trace.json()
    assert body["status"] == "alerted"
    assert body["prompt_version"]
    assert body["turns"]
    assert any(t.get("tool") and t.get("arguments") is not None for t in body["turns"])
    assert any(t.get("checks") for t in body["turns"])


def test_hidden_superseded_and_abandoned_runs(client: TestClient, seeded_alert: dict) -> None:
    session: Session = seeded_alert["session"]
    event: Event = seeded_alert["event"]
    run: AgentRun = seeded_alert["run"]
    alert = session.scalars(select(Alert).where(Alert.run_id == run.id)).one()

    run.status = "abandoned"
    session.flush()
    new_run = start_agent_run(session, event)
    session.commit()
    assert new_run.status == "running"

    assert client.get(f"/api/runs/{run.id}/trace").status_code == 404
    assert client.get(f"/api/alerts/{alert.id}").status_code == 404
    listed = client.get("/api/alerts", params={"issuer": "verizon"})
    assert all(a["id"] != alert.id for a in listed.json()["items"])

    assert (
        client.get(f"/api/runs/{run.id}/trace", params={"include_superseded": True}).status_code
        == 200
    )
    assert (
        client.get(f"/api/alerts/{alert.id}", params={"include_superseded": True}).status_code
        == 200
    )

    run.status = "superseded"
    session.commit()
    assert client.get(f"/api/runs/{run.id}").status_code == 404
    assert (
        client.get(f"/api/runs/{run.id}", params={"include_superseded": "true"}).status_code == 200
    )


def test_replay_status(client: TestClient, api_db: Session, tmp_path: Path, monkeypatch) -> None:
    load_seed(api_db, SEED_PATH)
    api_db.add(
        Event(
            replay_key="api_replay_count",
            event_type="other",
            event_date=date(2026, 1, 2),
            summary="count me",
            status="pending",
        )
    )
    api_db.commit()

    # Older stamp should lose to stable latest_eval.json.
    older = tmp_path / "20260101T000000Z_eval.json"
    older.write_text(
        json.dumps(
            {
                "incomplete": True,
                "alert": {"precision": 0.1, "recall": 0.1},
                "totals": {"cost_usd": 1.0},
            }
        ),
        encoding="utf-8",
    )
    latest = tmp_path / "latest_eval.json"
    latest.write_text(
        json.dumps(
            {
                "incomplete": False,
                "alert": {"precision": 0.8, "recall": 0.75},
                "totals": {"cost_usd": 12.5},
                "evaluated_at": "2026-10-30T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.replay.DEFAULT_RUNS_DIR", tmp_path)

    resp = client.get("/api/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_counts_by_status"].get("pending", 0) >= 1
    assert body["latest_eval"] is not None
    assert body["latest_eval"]["precision"] == 0.8
    assert body["latest_eval"]["recall"] == 0.75
    assert body["latest_eval"]["cost_usd"] == 12.5
    assert body["latest_eval"]["incomplete"] is False
    assert body["latest_eval"]["source_path"].endswith("latest_eval.json")
    dumped = json.dumps(body)
    assert "expected_alert" not in dumped
    assert "expected_changes" not in dumped
