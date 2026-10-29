"""Agent loop (§9.2): FakeModel scripts, no network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.fake import FakeModel, function_call, multi_call, plain_text
from app.agent.loop import (
    RunLimits,
    assemble_user_input,
    resume_run,
    run_event,
)
from app.agent.text import html_to_text
from app.db import Base
from app.models import (
    Alert,
    Event,
    EventFiling,
    Filing,
    Instrument,
    Issuer,
    ReplayExpectation,
)
from app.models.trace import AgentStep, ModelCall
from scripts.load_seed import load_seed

# Reuse Verizon quotes / checklist from the tools suite.
from tests.test_agent_tools import REDEMPTION_ITEMS, REDEMPTION_QUOTES

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"
REDEMPTION_HTML = (
    Path(__file__).resolve().parent / "fixtures" / "newsroom" / "verizon_redemption_2026-08-20.html"
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def verizon_event(session: Session) -> tuple[Event, Filing, Instrument]:
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
        content_hash="fixture",
    )
    session.add(filing)
    session.flush()
    event = Event(
        replay_key="vz_redemption_notice",
        event_type="press_release",
        issuer_id=verizon.id,
        event_date=date(2026, 8, 20),
        source_filing_id=filing.id,
        summary="Verizon Communications Inc., press release, 20 Aug 2026",
        status="pending",
    )
    session.add(event)
    session.flush()
    session.add(EventFiling(event_id=event.id, filing_id=filing.id))
    session.add(
        ReplayExpectation(
            key="vz_redemption_notice",
            issuer_key="verizon",
            event_type="redemption_notice",
            event_date=date(2026, 8, 20),
            available_from=date(2026, 8, 20),
            title="Verizon will redeem all $1.25bn of its 4.329% notes due 2028 on 21 Sep 2026",
            sources=[{"url": filing.url, "form": "press release", "filed": "2026-08-20"}],
            expected_changes=[
                {
                    "action": "mark_redeemed",
                    "instrument_key": "vz_4329_2028",
                    "effective": "2026-09-21",
                }
            ],
            expected_alert=True,
            why="Flagship alert: removes $1.25bn from Verizon's 2028 maturities",
            event_id=event.id,
        )
    )
    session.commit()
    return event, filing, bond


def test_assemble_user_input_excludes_expectation_text(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    text = assemble_user_input(session, event)
    assert "Flagship alert" not in text
    assert "expected_changes" not in text
    assert "expected_alert" not in text
    assert "ReplayExpectation" not in text
    assert "why" not in json.loads(text).get("event", {})
    payload = json.loads(text)
    assert payload["event"]["event_type"] == "press_release"
    assert payload["issuer"]["name"]
    assert payload["watchlist"]["on_watchlist"] is True


def test_event_v2_prompt_inserts_available_from(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    from app.agent.prompts import PROMPT_VERSION, load_event_prompt
    from app.agent.tools.debt import available_from_for_event

    event, _filing, _bond = verizon_event
    as_of = available_from_for_event(session, event)
    text = load_event_prompt(today=as_of)
    assert PROMPT_VERSION == "event_v2"
    assert "2026-08-20" in text
    assert "{available_from}" not in text
    assert "do not use knowledge of anything after today" in text


def _verizon_script(filing_id: str) -> list:
    """Scripted Responses turns for the full redemption path."""

    script: list = [
        function_call(
            "set_checklist",
            {"kind": "redemption", "items": REDEMPTION_ITEMS},
            commentary="setting checklist",
        ),
        function_call("list_documents", {}, commentary="listing docs"),
        function_call(
            "read_document",
            {"filing_id": filing_id, "page": 0},
            commentary="reading press release",
        ),
    ]
    for key, quote in REDEMPTION_QUOTES.items():
        script.append(
            function_call(
                "record_fact",
                {
                    "item_key": key,
                    "filing_id": filing_id,
                    "quote": quote,
                    "claim": f"claim for {key}",
                },
                commentary=f"recording {key}",
            )
        )

    def propose(ctx):
        facts = ctx.fact_ids()
        return function_call(
            "propose_change",
            {
                "change": {
                    "action": "mark_redeemed",
                    "instrument_key": "vz_4329_2028",
                    "effective_date": "2026-09-21",
                    "evidence": {
                        "instrument": facts["instrument"],
                        "effective_date": facts["redemption_date"],
                        "amount": None,
                        "name": None,
                        "maturity": None,
                        "coupon": None,
                        "issue_date": None,
                        "amount_outstanding": None,
                    },
                }
            },
            commentary="proposing mark_redeemed",
        )

    script.append(propose)
    script.append(function_call("compute_metrics", {}, commentary="metrics"))

    def alert(ctx):
        facts = ctx.fact_ids()
        findings = [
            {
                "item_key": key,
                "statement": (f"Settled {key} with amount $1,250,000,000 on September 21, 2026."),
                "citations": [facts[key]],
            }
            for key in facts
        ]
        return function_call(
            "submit_alert",
            {
                "summary": (
                    "Verizon will redeem all $1,250,000,000 of its 4.329% Notes due 2028 "
                    "on September 21, 2026, cutting the FY2028 wall by $1,250,000,000."
                ),
                "findings": findings,
                "material": True,
                "judgement_reason": None,
            },
            commentary="submitting alert",
        )

    script.append(alert)
    return script


@pytest.mark.asyncio
async def test_verizon_redemption_loop_ends_alerted(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, bond = verizon_event
    client = FakeModel(_verizon_script(filing.id))
    run = await run_event(session, event.id, client, RunLimits(max_turns=40))
    assert run.status == "alerted", (run.status, run.error, run.result)
    alert = session.scalars(select(Alert).where(Alert.event_id == event.id)).one()
    assert alert.status == "new"
    session.refresh(bond)
    assert bond.status == "redeemed"
    assert client.records
    assert any(s.output and s.output.get("response_id") for s in run.steps)
    # Every turn has a ModelCall linked to its committed AgentStep.
    steps = session.scalars(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
    ).all()
    assert steps
    for step in steps:
        mc = session.scalars(select(ModelCall).where(ModelCall.step_id == step.id)).one()
        assert mc.run_id == run.id
        assert mc.input_tokens is not None
    # Full transcript every turn; never previous_response_id.
    assert all(r.get("previous_response_id") is None for r in client.requests)
    assert len(client.requests[-1]["input"]) >= 1


@pytest.mark.asyncio
async def test_plain_text_three_times_pauses(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    client = FakeModel(
        [
            plain_text("I think this is a redemption"),
            plain_text("Still thinking"),
            plain_text("No tool yet"),
        ]
    )
    run = await run_event(session, event.id, client, RunLimits(max_turns=40))
    assert run.status == "paused"
    assert "without a tool call" in (run.error or "")
    assert len(run.steps) == 3
    assert (run.result or {}).get("prompt_version") == "event_v2"
    assert (run.result or {}).get("available_from") == "2026-08-20"


@pytest.mark.asyncio
async def test_two_calls_in_one_turn_runs_only_first(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    client = FakeModel(
        [
            multi_call(
                [
                    ("list_documents", {}),
                    ("read_document", {"filing_id": filing.id, "page": 0}),
                ]
            ),
            # After first tool, next turn: plain texts to stop without needing full path
            plain_text("a"),
            plain_text("b"),
            plain_text("c"),
        ]
    )
    run = await run_event(session, event.id, client, RunLimits(max_turns=40))
    assert run.status == "paused"
    first = run.steps[0]
    assert first.output is not None
    assert first.output.get("tool") == "list_documents"
    assert first.output.get("extra_calls_refused") == 1
    assert first.output.get("result", {}).get("ok") is True
    # Second call must not have read the document (pages_read empty or no filing)
    assert not (run.pages_read or {}).get(filing.id)


@pytest.mark.asyncio
async def test_turn_limit_pauses(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    client = FakeModel(
        [
            function_call("list_documents", {}),
            function_call("list_documents", {}),
            function_call("list_documents", {}),
        ]
    )
    run = await run_event(session, event.id, client, RunLimits(max_turns=2))
    assert run.status == "paused"
    assert "turn limit" in (run.error or "")
    assert len(run.steps) == 2


@pytest.mark.asyncio
async def test_resume_after_pause(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    # Pause via turn limit after listing docs once.
    client = FakeModel([function_call("list_documents", {})])
    run = await run_event(session, event.id, client, RunLimits(max_turns=1))
    assert run.status == "paused"
    run_id = run.id

    # Resume with read + then three plain texts → pause again.
    client2 = FakeModel(
        [
            function_call(
                "read_document",
                {"filing_id": filing.id, "page": 0},
                commentary="resume read",
            ),
            plain_text("x"),
            plain_text("y"),
            plain_text("z"),
        ]
    )
    run2 = await resume_run(session, run_id, client2, RunLimits(max_turns=40))
    assert run2.id == run_id
    assert run2.status == "paused"
    assert "without a tool call" in (run2.error or "")
    # Resume always sends the saved full transcript (no previous_response_id).
    assert all(r.get("previous_response_id") is None for r in client2.requests)
    assert isinstance(client2.requests[0]["input"], list)
    assert len(client2.requests[0]["input"]) >= 2
    assert any(s.output and s.output.get("tool") == "read_document" for s in run2.steps)


@pytest.mark.asyncio
async def test_token_ceiling_pauses(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    heavy = function_call("list_documents", {})
    heavy["usage"] = {"input_tokens": 300_000, "output_tokens": 150_000}
    client = FakeModel([heavy, function_call("list_documents", {})])
    run = await run_event(session, event.id, client, RunLimits(max_turns=40, max_tokens=400_000))
    assert run.status == "paused"
    assert "token ceiling" in (run.error or "")
    assert (run.result or {}).get("total_tokens", 0) >= 400_000
