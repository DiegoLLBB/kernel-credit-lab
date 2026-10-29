"""Offline replay runner + evaluation (FakeModel, no network)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.fake import FakeModel, function_call
from app.agent.loop import RunLimits
from app.agent.text import html_to_text
from app.db import Base
from app.eval.replay_eval import evaluate_replay, write_eval_reports
from app.ingest.replay import ReplayClients, load_replay_events
from app.models import Alert, Event, Instrument
from app.models.trace import AgentRun
from app.replay.runner import run_replay
from scripts.load_seed import load_seed
from tests.test_agent_loop import _verizon_script
from tests.test_replay import FakeFetcher

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_JSON = FIXTURES / "replay" / "mini_replay_3.json"
SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"
REDEMPTION_HTML = FIXTURES / "newsroom" / "verizon_redemption_2026-08-20.html"
LOG_HTML = FIXTURES / "newsroom" / "mini_log_notice.html"

LOG_A_URL = "https://www.verizon.com/about/news/fixture-log-a"
LOG_B_URL = "https://www.verizon.com/about/news/fixture-log-b"
REDEMPTION_URL = "https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026"

LOG_QUOTES = {
    "subject": "Subject bond announcement appears in this notice.",
    "effect": "Effect on near-term maturities is none within the horizon.",
    "counter_evidence": "Counter evidence: no near-term maturity is stated.",
}

OTHER_ITEMS = [
    {
        "key": "subject",
        "question": "What is the subject?",
        "evidence_needed": "subject sentence",
    },
    {
        "key": "effect",
        "question": "Near-term effect?",
        "evidence_needed": "effect sentence",
    },
    {
        "key": "counter_evidence",
        "question": "Any counter evidence?",
        "evidence_needed": "counter sentence",
    },
]


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def seeded(session: Session) -> Session:
    load_seed(session, SEED_PATH)
    session.commit()
    return session


@pytest.fixture()
def clients() -> ReplayClients:
    # Store plain text (as agent fixtures do); raw HTML would not match quote checks.
    log_text = html_to_text(LOG_HTML.read_text(encoding="utf-8"))
    redemption = html_to_text(REDEMPTION_HTML.read_text(encoding="utf-8"))
    newsroom = FakeFetcher(
        {
            LOG_A_URL: log_text,
            LOG_B_URL: log_text,
            REDEMPTION_URL: redemption,
        }
    )
    return ReplayClients(edgar=FakeFetcher({}), newsroom=newsroom)


def _logged_script(filing_id: str) -> list:
    script: list = [
        function_call(
            "set_checklist",
            {"kind": "other", "items": OTHER_ITEMS},
            commentary="checklist",
        ),
        function_call("list_documents", {}, commentary="docs"),
        function_call(
            "read_document",
            {"filing_id": filing_id, "page": 0},
            commentary="read",
        ),
    ]
    for key, quote in LOG_QUOTES.items():
        script.append(
            function_call(
                "record_fact",
                {
                    "item_key": key,
                    "filing_id": filing_id,
                    "quote": quote,
                    "claim": f"claim for {key}",
                },
                commentary=f"fact {key}",
            )
        )
    script.append(function_call("compute_metrics", {}, commentary="metrics"))

    def alert(ctx):
        facts = ctx.fact_ids()
        findings = [
            {
                "item_key": key,
                "statement": f"Settled {key} from the notice.",
                "citations": [facts[key]],
            }
            for key in facts
        ]
        return function_call(
            "submit_alert",
            {
                "summary": ("Issuer announcement logged; no near-term maturity impact."),
                "findings": findings,
                "material": False,
                "judgement_reason": None,
            },
            commentary="log",
        )

    script.append(alert)
    return script


def _client_factory(
    session: Session,
    *,
    cost_per_call: Decimal = Decimal("0.000001"),
    reuse: bool = False,
):
    """Build a FakeModel per event from its linked filing.

    With ``reuse=True``, the same FakeModel instance is returned on resume so the
    script index continues (needed when a prior pass paused mid-script).
    """
    cache: dict[str, FakeModel] = {}

    def factory(event: Event) -> FakeModel:
        from app.models import EventFiling

        key = event.replay_key or event.id
        if reuse and key in cache:
            return cache[key]

        filing_id = event.source_filing_id
        if filing_id is None:
            link = session.scalars(
                select(EventFiling).where(EventFiling.event_id == event.id).limit(1)
            ).first()
            filing_id = link.filing_id if link else None
        if filing_id is None:
            raise AssertionError(f"event {event.replay_key} has no filing")
        if key == "vz_redemption_mini":
            script = _verizon_script(filing_id)
        else:
            script = _logged_script(filing_id)
        model = FakeModel(script, cost_per_call=cost_per_call)
        if reuse:
            cache[key] = model
        return model

    return factory


@pytest.mark.asyncio
async def test_mini_replay_log_and_eval(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    session = seeded
    load_replay_events(session, MINI_JSON)
    session.commit()

    result = await run_replay(
        session,
        clients,
        _client_factory(session),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path,
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )

    assert result.stopped_reason == "complete"
    assert result.log_path.exists()
    log = json.loads(result.log_path.read_text(encoding="utf-8"))
    keys = [e["replay_key"] for e in log["events"]]
    assert keys == ["vz_log_a", "vz_redemption_mini", "vz_log_b"]
    by_key = {e["replay_key"]: e for e in log["events"]}
    assert by_key["vz_log_a"]["status"] == "logged"
    assert by_key["vz_log_a"]["material"] is False
    assert by_key["vz_redemption_mini"]["status"] == "alerted"
    assert by_key["vz_redemption_mini"]["material"] is True
    assert by_key["vz_log_b"]["status"] == "logged"

    alert = session.scalars(
        select(Alert).where(
            Alert.event_id
            == session.scalars(select(Event).where(Event.replay_key == "vz_redemption_mini"))
            .one()
            .id
        )
    ).one()
    assert alert is not None
    bond = session.scalars(select(Instrument).where(Instrument.key == "vz_4329_2028")).one()
    assert bond.status == "redeemed"

    report = evaluate_replay(session, replay_path=MINI_JSON, log=log)
    assert report.alert.true_positive == 1
    assert report.alert.true_negative == 2
    assert report.alert.false_positive == 0
    assert report.alert.false_negative == 0
    assert report.alert.precision == pytest.approx(1.0)
    assert report.alert.recall == pytest.approx(1.0)
    assert report.alert.incomplete_expected_alert == 0

    change_matches = [c for c in report.changes if c.expectation_key == "vz_redemption_mini"]
    assert change_matches
    assert all(c.matched for c in change_matches if c.expected.get("action") != "log_only")

    md_path, json_path = write_eval_reports(report, result.log_path)
    assert md_path.exists() and json_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "Precision" in md
    assert "Confusion table" in md


@pytest.mark.asyncio
async def test_replay_resumes_after_interrupt(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    session = seeded
    load_replay_events(session, MINI_JSON)
    session.commit()
    factory = _client_factory(session, reuse=True)

    # First pass: only enough turns for the first event to pause mid-script.
    result1 = await run_replay(
        session,
        clients,
        factory,
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path / "pass1",
        limits=RunLimits(max_turns=2, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result1.events
    first = result1.events[0]
    assert first.replay_key == "vz_log_a"
    assert first.status == "paused"

    # Second pass: higher turn limit; resumes paused run then continues.
    result2 = await run_replay(
        session,
        clients,
        factory,
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path / "pass2",
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result2.stopped_reason == "complete"
    for key in ("vz_log_a", "vz_redemption_mini", "vz_log_b"):
        event = session.scalars(select(Event).where(Event.replay_key == key)).one()
        run = session.get(AgentRun, event.run_id)
        assert run is not None
        assert run.status in {"alerted", "logged"}, (key, run.status, run.error)


@pytest.mark.asyncio
async def test_replay_cost_limit_stops_cleanly(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    session = seeded
    load_replay_events(session, MINI_JSON)
    session.commit()

    # ~7 model calls per logged event at $1 each → stop after the first event.
    result = await run_replay(
        session,
        clients,
        _client_factory(session, cost_per_call=Decimal("1")),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path,
        max_cost_usd=5.0,
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result.stopped_reason == "cost_limit"
    assert result.total_cost_usd >= 5.0
    keys_done = [e.replay_key for e in result.events]
    assert "vz_log_a" in keys_done
    assert "vz_log_b" not in keys_done

    # Remaining events still pending or uncreated for later days.
    log_b = session.scalars(select(Event).where(Event.replay_key == "vz_log_b")).first()
    assert log_b is None or log_b.status == "pending"


@pytest.mark.asyncio
async def test_rate_update_logged_by_code(seeded: Session, tmp_path: Path) -> None:
    """rate_update events never call the model."""
    from app.replay.runner import complete_rate_update_by_code

    session = seeded
    event = Event(
        replay_key="rate_update:2026-05-15",
        event_type="rate_update",
        issuer_id=None,
        event_date=date(2026, 5, 15),
        summary="Rate update, 15 May 2026",
        status="pending",
    )
    session.add(event)
    session.commit()

    calls: list[str] = []

    def factory(_event: Event) -> FakeModel:
        calls.append("called")
        return FakeModel([])

    # Direct code path
    run = complete_rate_update_by_code(session, event)
    session.commit()
    assert run.status == "logged"
    assert (run.result or {}).get("by") == "code"
    assert event.status == "processed"
    assert calls == []
