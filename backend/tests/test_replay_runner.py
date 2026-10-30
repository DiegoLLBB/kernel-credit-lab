"""Offline replay runner + evaluation (FakeModel, no network)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.fake import FakeModel, function_call
from app.agent.loop import RunLimits, run_event
from app.agent.registry import dispatch_tool
from app.agent.run import start_agent_run
from app.agent.text import html_to_text
from app.db import Base
from app.eval.replay_eval import evaluate_replay, write_eval_reports
from app.ingest.replay import ReplayClients, load_replay_events
from app.models import Alert, AuditEntry, Event, EventFiling, Filing, Instrument, Issuer
from app.models.trace import AgentRun, ToolCall
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

    # First pass: only enough turns for events to pause mid-script — replay continues.
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
    assert result1.stopped_reason == "complete"
    first = next(e for e in result1.events if e.replay_key == "vz_log_a")
    assert first.status == "paused"
    assert {e.replay_key for e in result1.events} >= {
        "vz_log_a",
        "vz_redemption_mini",
        "vz_log_b",
    }

    # Second pass: higher turn limit; resumes paused runs then continues.
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
async def test_replay_max_cost_ignores_prior_db_runs(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    """--max-cost-usd counts only this invocation, not historical AgentRun spend."""
    session = seeded
    issuer = session.scalars(select(Issuer).limit(1)).one()
    # ~$20 already spent on earlier event-linked runs (the old bug summed these).
    for i in range(4):
        prior_event = Event(
            replay_key=f"prior_spend_{i}",
            event_type="other",
            issuer_id=issuer.id,
            event_date=date(2026, 1, 1),
            summary=f"prior spend {i}",
            status="processed",
        )
        session.add(prior_event)
        session.flush()
        prior_run = AgentRun(
            question=f"prior spend {i}",
            workflow="event_monitor",
            mode="standard",
            status="logged",
            state="DONE",
            event_id=prior_event.id,
            total_cost_usd=Decimal("5.00"),
            metrics_outputs=[],
        )
        session.add(prior_run)
        session.flush()
        prior_event.run_id = prior_run.id
    session.commit()

    load_replay_events(session, MINI_JSON)
    session.commit()

    result = await run_replay(
        session,
        clients,
        _client_factory(session, cost_per_call=Decimal("0.000001")),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path,
        max_cost_usd=5.0,
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    # Old behaviour: total_cost seeded from DB (~$20) → immediate cost_limit, 0 events.
    assert result.stopped_reason == "complete"
    assert result.total_cost_usd < 5.0
    assert {e.replay_key for e in result.events} >= {
        "vz_log_a",
        "vz_redemption_mini",
        "vz_log_b",
    }


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


@pytest.mark.asyncio
async def test_maturity_handled_by_code(seeded: Session) -> None:
    """Calendar maturity events mark active instruments matured with no model call."""
    from app.models import Issuer, StateChange
    from app.replay.runner import complete_maturity_by_code, process_one_event

    session = seeded
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    bond = Instrument(
        key="vz_mat_test_2026",
        issuer_id=verizon.id,
        name="Test Notes due 2026",
        coupon=Decimal("3.000"),
        maturity=date(2026, 6, 15),
        amount_outstanding=Decimal("1000000000"),
        currency="USD",
        instrument_type="bond",
        status="active",
    )
    session.add(bond)
    session.flush()
    event = Event(
        replay_key=f"maturity:{bond.key}:2026-06-15",
        event_type="maturity",
        issuer_id=verizon.id,
        event_date=date(2026, 6, 15),
        summary="Test maturity",
        status="pending",
    )
    session.add(event)
    session.commit()

    # Client must not be used for maturity (code path).
    client = FakeModel([])
    outcome = await process_one_event(session, event, client)
    assert outcome.by_code is True
    assert outcome.status == "logged"
    assert client._index == 0  # noqa: SLF001 — no model turns consumed
    session.refresh(bond)
    assert bond.status == "matured"
    change = session.scalars(
        select(StateChange).where(StateChange.event_id == event.id, StateChange.status == "applied")
    ).one()
    assert change.proposed_diff["action"] == "mark_matured"
    assert change.proposed_diff["source"] == "scheduled maturity per Kernel records"
    assert change.effective_date == date(2026, 6, 15)

    # Already matured → log "already updated"
    event2 = Event(
        replay_key=f"maturity:{bond.key}:2026-06-15:again",
        event_type="maturity",
        issuer_id=verizon.id,
        event_date=date(2026, 6, 15),
        summary="Repeat maturity",
        status="pending",
    )
    session.add(event2)
    session.commit()
    run2 = complete_maturity_by_code(session, event2)
    session.commit()
    assert run2.status == "logged"
    assert (run2.result or {}).get("reason") == "already updated"


def test_max_tokens_for_event_short_vs_long() -> None:
    from app.agent.loop import MAX_RUN_TOKENS, MAX_RUN_TOKENS_LONG, max_tokens_for_event

    short = Event(
        replay_key="x",
        event_type="8-K",
        event_date=date(2026, 1, 1),
        summary="s",
        status="pending",
    )
    long = Event(
        replay_key="y",
        event_type="10-Q",
        event_date=date(2026, 1, 1),
        summary="s",
        status="pending",
    )
    assert max_tokens_for_event(short) == MAX_RUN_TOKENS
    assert max_tokens_for_event(long) == MAX_RUN_TOKENS_LONG


def test_start_agent_run_abandons_stale_running(seeded: Session) -> None:
    session = seeded
    event, _ = _minimal_event(session, replay_key="abandon_probe")
    first = start_agent_run(session, event)
    session.commit()
    assert first.status == "running"
    second = start_agent_run(session, event)
    session.commit()
    session.refresh(first)
    assert first.status == "abandoned"
    assert second.status == "running"
    assert event.run_id == second.id


@pytest.mark.asyncio
async def test_rerun_supersedes_prior_and_keeps_others(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    """``--rerun`` marks old runs superseded, resets those events, leaves others alone."""
    session = seeded
    # Full mini pass first.
    result1 = await run_replay(
        session,
        clients,
        _client_factory(session),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path / "pass1",
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result1.stopped_reason == "complete"
    log_a = session.scalars(select(Event).where(Event.replay_key == "vz_log_a")).one()
    redemption = session.scalars(
        select(Event).where(Event.replay_key == "vz_redemption_mini")
    ).one()
    old_log_run_id = log_a.run_id
    old_redemption_run_id = redemption.run_id
    assert old_log_run_id and old_redemption_run_id
    old_log_run = session.get(AgentRun, old_log_run_id)
    assert old_log_run is not None and old_log_run.status == "logged"

    result2 = await run_replay(
        session,
        clients,
        _client_factory(session),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        rerun_keys={"vz_log_a"},
        replay_path=MINI_JSON,
        runs_dir=tmp_path / "pass2",
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result2.stopped_reason == "complete"
    assert result2.rerun_keys == ["vz_log_a"]
    session.refresh(log_a)
    session.refresh(redemption)
    session.refresh(old_log_run)
    assert old_log_run.status == "superseded"
    assert log_a.run_id != old_log_run_id
    new_run = session.get(AgentRun, log_a.run_id)
    assert new_run is not None and new_run.status == "logged"
    # Other events untouched.
    assert redemption.run_id == old_redemption_run_id
    red_run = session.get(AgentRun, old_redemption_run_id)
    assert red_run is not None and red_run.status == "alerted"
    selected = [e for e in result2.events if not e.skipped]
    assert [e.replay_key for e in selected] == ["vz_log_a"]


@pytest.mark.asyncio
async def test_only_leaves_other_events_pending(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    """``--only`` must not mark non-selected events processed."""
    session = seeded
    load_replay_events(session, MINI_JSON)
    session.commit()

    result = await run_replay(
        session,
        clients,
        _client_factory(session),
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        only_keys={"vz_redemption_mini"},
        replay_path=MINI_JSON,
        runs_dir=tmp_path,
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=None,
    )
    assert result.stopped_reason == "complete"
    assert result.only_keys == ["vz_redemption_mini"]

    redemption = session.scalars(
        select(Event).where(Event.replay_key == "vz_redemption_mini")
    ).one()
    run = session.get(AgentRun, redemption.run_id)
    assert run is not None and run.status == "alerted"

    for key in ("vz_log_a", "vz_log_b"):
        event = session.scalars(select(Event).where(Event.replay_key == key)).one()
        assert event.status == "pending"
        assert event.run_id is None

    skipped = [e for e in result.events if e.skipped]
    assert {e.replay_key for e in skipped} == {"vz_log_a", "vz_log_b"}
    assert all(e.skip_reason == "not selected" for e in skipped)

    log = json.loads(result.log_path.read_text(encoding="utf-8"))
    report = evaluate_replay(session, replay_path=MINI_JSON, log=log)
    keys = {r.replay_key for r in report.per_event}
    assert keys == {"vz_redemption_mini"}
    assert report.alert.true_positive == 1
    assert report.alert.true_negative == 0


def test_needs_input_line_includes_question() -> None:
    from app.replay.runner import EventOutcome, format_event_line

    line = format_event_line(
        EventOutcome(
            replay_key="intc_bridge",
            event_id="x",
            event_date="2026-05-01",
            issuer="intel",
            event_type="8-K",
            status="needs_input",
            material=None,
            turns=10,
            tokens=1000,
            cost_usd=0.1,
            duration_s=12.0,
            run_id="r1",
            detail="What is the maturity date of the bridge facility drawn in March?",
        )
    )
    assert "needs_input" in line
    assert "What is the maturity date of the bridge facility" in line


def _minimal_event(
    session: Session, *, replay_key: str = "tool_error_probe", with_filing: bool = False
) -> tuple[Event, Filing | None]:
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    event = Event(
        replay_key=replay_key,
        event_type="other",
        issuer_id=verizon.id,
        event_date=date(2026, 5, 11),
        summary="Minimal event for tool-error tests",
        status="pending",
    )
    session.add(event)
    session.flush()
    filing: Filing | None = None
    if with_filing:
        filing = Filing(
            issuer_id=verizon.id,
            form_type="press_release",
            url=f"https://example.test/{replay_key}",
            filed_at=date(2026, 5, 11),
            title="Fixture notice",
            body_text=LOG_QUOTES["subject"] + " " + LOG_QUOTES["effect"],
            content_hash=replay_key,
        )
        session.add(filing)
        session.flush()
        session.add(EventFiling(event_id=event.id, filing_id=filing.id))
    session.commit()
    return event, filing


@pytest.mark.asyncio
async def test_dispatch_tool_catches_exception(seeded: Session) -> None:
    """Tools never raise: exceptions become refusals with audit + ToolCall."""
    session = seeded
    event, _ = _minimal_event(session)
    run = start_agent_run(session, event)
    session.commit()

    with patch(
        "app.agent.registry.list_documents",
        side_effect=RuntimeError("boom"),
    ):
        result = await dispatch_tool(session, run, "list_documents", {})

    assert result["ok"] is False
    assert result["refused"] is True
    assert result["error"] == "internal error in list_documents: boom"
    tc = session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at.desc())
    ).first()
    assert tc is not None
    assert tc.status == "error"
    assert "boom" in (tc.error or "")
    assert "traceback" in (tc.result or {})
    audit = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.run_id == run.id, AuditEntry.action == "tool:list_documents:error")
        .order_by(AuditEntry.timestamp.desc())
    ).first()
    assert audit is not None


@pytest.mark.asyncio
async def test_tool_raise_mid_run_continues(seeded: Session) -> None:
    """A tool exception returns an error result to the agent; the run continues."""
    session = seeded
    event, filing = _minimal_event(session, replay_key="tool_raise_mid_run", with_filing=True)
    assert filing is not None

    from app.agent.tools import list_documents as real_list_documents

    calls = {"n": 0}

    def flaky_list(session_: Session, run_: AgentRun) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient tool failure")
        return real_list_documents(session_, run_)

    script = [
        function_call(
            "set_checklist",
            {"kind": "other", "items": OTHER_ITEMS},
            commentary="checklist",
        ),
        function_call("list_documents", {}, commentary="first list (will fail)"),
        function_call("list_documents", {}, commentary="retry list"),
        function_call(
            "read_document",
            {"filing_id": filing.id, "page": 0},
            commentary="read after recovery",
        ),
        function_call(
            "request_input",
            {"item_key": "subject", "question": "Need human help after tool error?"},
            commentary="asking",
        ),
    ]
    client = FakeModel(script)

    with patch("app.agent.registry.list_documents", side_effect=flaky_list):
        run = await run_event(
            session,
            event.id,
            client,
            RunLimits(max_turns=20, max_cost_usd=100.0, max_tokens=10_000_000),
        )
    session.commit()

    assert calls["n"] == 2
    assert run.status == "needs_input"
    errors = session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.status == "error")
    ).all()
    assert len(errors) == 1
    assert "transient tool failure" in (errors[0].error or "")
    ok_lists = session.scalars(
        select(ToolCall).where(
            ToolCall.run_id == run.id,
            ToolCall.tool_name == "list_documents",
            ToolCall.status == "success",
        )
    ).all()
    assert len(ok_lists) == 1


@pytest.mark.asyncio
async def test_runner_isolates_event_failure_and_continues(
    seeded: Session, clients: ReplayClients, tmp_path: Path
) -> None:
    """An exception while processing one event marks it failed; later events still run."""
    session = seeded
    load_replay_events(session, MINI_JSON)
    session.commit()

    real_factory = _client_factory(session)
    crash_keys = {"vz_log_a"}

    def factory(event: Event) -> FakeModel:
        if event.replay_key in crash_keys:
            raise RuntimeError("simulated event crash")
        return real_factory(event)

    lines: list[str] = []
    result = await run_replay(
        session,
        clients,
        factory,
        window_from=date(2026, 5, 11),
        window_until=date(2026, 5, 13),
        replay_path=MINI_JSON,
        runs_dir=tmp_path,
        limits=RunLimits(max_turns=40, max_cost_usd=100.0, max_tokens=10_000_000),
        print_line=lines.append,
    )

    assert result.stopped_reason == "complete"
    by_key = {e.replay_key: e for e in result.events}
    assert by_key["vz_log_a"].status == "failed"
    assert "simulated event crash" in (by_key["vz_log_a"].detail or "")
    assert by_key["vz_redemption_mini"].status == "alerted"
    assert by_key["vz_log_b"].status == "logged"
    assert any("failed" in line and "simulated event crash" in line for line in lines)

    failed_event = session.scalars(select(Event).where(Event.replay_key == "vz_log_a")).one()
    assert failed_event.status == "failed"
    failed_run = session.get(AgentRun, failed_event.run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert "simulated event crash" in (failed_run.error or "")
    assert "traceback" in (failed_run.result or {})

    log = json.loads(result.log_path.read_text(encoding="utf-8"))
    report = evaluate_replay(session, replay_path=MINI_JSON, log=log)
    assert report.alert.failed_expected_no_alert == 1
    assert report.alert.failed_expected_alert == 0
    assert report.alert.true_positive == 1
    assert report.alert.true_negative == 1
    assert report.alert.incomplete_expected_alert == 0
    assert report.alert.incomplete_expected_no_alert == 0
