"""Agent loop (§9.2): FakeModel scripts, no network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.fake import FakeModel, function_call, multi_call, plain_text
from app.agent.loop import (
    BUDGET_LOW_MSG,
    DEFAULT_MAX_TURNS,
    MAX_RUN_TOKENS,
    MAX_RUN_TOKENS_LONG,
    PERIODIC_MAX_TURNS,
    RunLimits,
    assemble_user_input,
    attach_run_budget,
    compact_transcript_for_model,
    max_tokens_for_event,
    max_turns_for_event,
    resume_run,
    run_event,
)
from app.agent.text import html_to_text
from app.config import Settings
from app.db import Base
from app.llm.client import LLMClient
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


def test_event_v9_prompt_inserts_available_from(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    from app.agent.prompts import PROMPT_VERSION, load_event_prompt
    from app.agent.tools.debt import available_from_for_event

    event, _filing, _bond = verizon_event
    as_of = available_from_for_event(session, event)
    text = load_event_prompt(today=as_of)
    assert PROMPT_VERSION == "event_v9"
    assert "2026-08-20" in text
    assert "{available_from}" not in text
    assert "do not use knowledge of anything after today" in text
    assert "search for the terms you need" in text
    assert "one short factual sentence" in text
    assert "cite their IDs in" in text and "propose_change.evidence" in text
    assert "do not re-read pages you have already quoted" in text
    assert "amount_scale" in text
    assert "YYYY-MM" in text
    assert "Never add or combine figures yourself" in text
    assert "settlement date" in text
    assert "don't wait for settlement" in text
    assert "bridge facilities" in text
    assert "announced / priced / closed / settled" in text
    assert (
        "search_document` for debt terms" in text or "Use `search_document` for debt terms" in text
    )
    assert "Stop investigating once every checklist item has a fact" in text
    assert "turns_left" in text and "tokens_left" in text
    assert "New debt maturing after 2029" in text
    assert "logged, not alerted" in text
    # Prior versions kept on disk
    v8 = load_event_prompt(version="event_v8", today=as_of)
    assert "Stop investigating once every checklist item has a fact" not in v8
    assert "bridge facilities" in v8
    assert "New debt maturing after 2029" not in v8


def test_attach_run_budget_warns_below_quarter() -> None:
    ok = attach_run_budget(
        {"ok": True},
        turns_left=20,
        tokens_left=300_000,
        max_turns=40,
        max_tokens=400_000,
    )
    assert ok["turns_left"] == 20 and ok["tokens_left"] == 300_000
    assert "budget_warning" not in ok

    low_turns = attach_run_budget(
        {"ok": True},
        turns_left=5,
        tokens_left=300_000,
        max_turns=40,
        max_tokens=400_000,
    )
    assert low_turns["budget_warning"] == BUDGET_LOW_MSG

    low_tokens = attach_run_budget(
        {"ok": True},
        turns_left=30,
        tokens_left=50_000,
        max_turns=40,
        max_tokens=400_000,
    )
    assert low_tokens["budget_warning"] == BUDGET_LOW_MSG


def test_max_turns_for_event_periodic_vs_other(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    assert event.event_type == "press_release"
    assert max_turns_for_event(event) == DEFAULT_MAX_TURNS
    assert max_tokens_for_event(event) == MAX_RUN_TOKENS
    event.event_type = "10-Q"
    assert max_turns_for_event(event) == PERIODIC_MAX_TURNS
    assert max_tokens_for_event(event) == MAX_RUN_TOKENS_LONG
    event.event_type = "periodic_filing"
    assert max_turns_for_event(event) == PERIODIC_MAX_TURNS
    assert max_turns_for_event(None) == DEFAULT_MAX_TURNS
    assert max_tokens_for_event(None) == MAX_RUN_TOKENS


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


class _HybridLoopClient:
    """FakeModel for /responses turns; real LLMClient.structured_json for claims check."""

    def __init__(self, fake: FakeModel, llm: LLMClient) -> None:
        self.fake = fake
        self.llm = llm
        self.settings = llm.settings

    async def responses(self, **kwargs: Any) -> Any:
        return await self.fake.responses(**kwargs)

    async def structured_json(self, **kwargs: Any) -> Any:
        return await self.llm.structured_json(**kwargs)

    def note_tool_result(self, result: dict[str, Any], *, item_key: str | None = None) -> None:
        self.fake.note_tool_result(result, item_key=item_key)

    @property
    def records(self) -> list:
        return self.fake.records

    @property
    def requests(self) -> list:
        return self.fake.requests


def _claims_pass_completion(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a chat-completions payload that marks summary + findings supported."""
    findings_n = 0
    item_keys: list[str | None] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "")
        marker = "Input JSON follows."
        if marker not in text:
            continue
        raw = text.split(marker, 1)[1].strip()
        try:
            payload = json.loads(raw)
            items = payload.get("findings") or []
            findings_n = len(items)
            item_keys = [it.get("item_key") for it in items]
        except json.JSONDecodeError:
            findings_n = 0
    body = {
        "summary": {"status": "supported", "unsupported_phrase": None},
        "findings": [
            {
                "finding_index": i,
                "item_key": item_keys[i] if i < len(item_keys) else None,
                "status": "supported",
                "unsupported_phrase": None,
            }
            for i in range(findings_n)
        ],
    }
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(body)}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 40},
    }


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
async def test_run_event_claims_check_reuses_loop_http_client(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    """Claims must await the agent's AsyncClient on the same loop (no asyncio.run)."""
    event, filing, bond = verizon_event
    chat_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/chat/completions" in str(request.url), request.url
        chat_calls.append(request)
        body = json.loads(request.content)
        assert body.get("temperature") == 0.0
        assert (body.get("response_format") or {}).get("type") == "json_schema"
        return httpx.Response(200, json=_claims_pass_completion(body.get("messages") or []))

    settings = Settings(
        model_api_key="test-key",
        meta_model_api_base_url="https://meta.test/v1",
        baseten_api_key="",  # force Meta path through the mocked transport
        default_model="muse-spark-1.3",
        llm_max_retries=0,
    )
    llm = LLMClient(settings, transport=httpx.MockTransport(handler))
    fake = FakeModel(_verizon_script(filing.id))
    client = _HybridLoopClient(fake, llm)
    try:
        run = await run_event(session, event.id, client, RunLimits(max_turns=40))
    finally:
        await llm.aclose()

    assert run.status == "alerted", (run.status, run.error, run.result)
    assert chat_calls, "claims check must hit chat/completions via LLMClient"
    session.refresh(bond)
    assert bond.status == "redeemed"
    alert = session.scalars(select(Alert).where(Alert.event_id == event.id)).one()
    assert alert.claims_check is not None
    assert alert.claims_check.get("status") == "pass"
    assert alert.claims_check.get("model") == "muse-spark-1.3"
    assert alert.claims_check.get("provider") == "meta"


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
    assert (run.result or {}).get("prompt_version") == "event_v9"
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
    steps_before = len(run.steps)
    sessions_after_first = list((run.result or {}).get("sessions") or [])
    assert len(sessions_after_first) == 1
    assert sessions_after_first[0].get("started_at")
    assert sessions_after_first[0].get("ended_at")

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
    assert len(run2.steps) > steps_before
    sessions = list((run2.result or {}).get("sessions") or [])
    assert len(sessions) == 2
    assert all(s.get("started_at") and s.get("ended_at") for s in sessions)


@pytest.mark.asyncio
async def test_resume_after_hour_ignores_original_wall_clock(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    """Wall-clock is per invocation: a run paused an hour ago can still resume."""
    from datetime import UTC, datetime, timedelta

    event, filing, _bond = verizon_event
    client = FakeModel([function_call("list_documents", {})])
    run = await run_event(
        session,
        event.id,
        client,
        RunLimits(max_turns=1, max_wall_seconds=600),
    )
    assert run.status == "paused"
    steps_before = len(run.steps)
    original_started = (run.result or {}).get("started_at")
    assert original_started

    # Pretend the first session started and ended an hour ago.
    hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    almost_hour = (datetime.now(UTC) - timedelta(hours=1) + timedelta(seconds=30)).isoformat()
    meta = dict(run.result or {})
    meta["started_at"] = hour_ago
    meta["sessions"] = [{"started_at": hour_ago, "ended_at": almost_hour}]
    meta["session_started_at"] = hour_ago
    meta["session_ended_at"] = almost_hour
    run.result = meta
    session.commit()

    client2 = FakeModel(
        [
            function_call(
                "read_document",
                {"filing_id": filing.id, "page": 0},
                commentary="resume after an hour",
            ),
            function_call("list_documents", {}, commentary="list again"),
            plain_text("x"),
            plain_text("y"),
            plain_text("z"),
        ]
    )
    run2 = await resume_run(
        session,
        run.id,
        client2,
        RunLimits(max_turns=40, max_wall_seconds=600),
    )
    assert run2.status == "paused"
    assert "wall-clock" not in (run2.error or "")
    assert "without a tool call" in (run2.error or "")
    assert len(run2.steps) > steps_before + 1
    # Original run start preserved; a new session was recorded.
    assert (run2.result or {}).get("started_at") == hour_ago
    sessions = list((run2.result or {}).get("sessions") or [])
    assert len(sessions) == 2
    assert sessions[0]["started_at"] == hour_ago
    assert sessions[1].get("started_at") != hour_ago
    assert sessions[1].get("ended_at")


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


def _read_call(call_id: str, page: int, filing_id: str, text: str) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "read_document",
            "arguments": json.dumps({"filing_id": filing_id, "page": page}),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(
                {
                    "ok": True,
                    "filing_id": filing_id,
                    "page": page,
                    "pages": 100,
                    "text": text,
                }
            ),
        },
    ]


def _search_call(call_id: str, filing_id: str, query: str, snippet: str) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "search_document",
            "arguments": json.dumps({"filing_id": filing_id, "query": query}),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(
                {
                    "ok": True,
                    "filing_id": filing_id,
                    "query": query,
                    "hit_count": 1,
                    "hits": [{"page": 0, "snippet": snippet}],
                }
            ),
        },
    ]


def test_compact_transcript_stubs_old_reads_and_searches() -> None:
    filing_id = "filing-abc"
    page_body = "X" * 8000
    transcript: list[dict] = [
        {"type": "message", "role": "user", "content": "event"},
    ]
    for i in range(6):
        transcript.extend(_read_call(f"read-{i}", i, filing_id, page_body))
    for i in range(5):
        transcript.extend(_search_call(f"search-{i}", filing_id, f"q{i}", "Y" * 400))

    page_facts = {
        (filing_id, 0): [
            {
                "fact_id": "fact-1",
                "item_key": "amount",
                "claim": "Carolinas term loan $1bn" + ("!" * 80),
            }
        ],
        (filing_id, 1): [{"fact_id": "fact-2", "item_key": "name", "claim": "DEC TL"}],
    }
    stored_before = json.dumps(transcript)
    compacted = compact_transcript_for_model(transcript, page_facts=page_facts)
    # Stored transcript unchanged.
    assert json.dumps(transcript) == stored_before

    read_outputs = [
        json.loads(item["output"])
        for item in compacted
        if item.get("type") == "function_call_output"
        and str(item.get("call_id", "")).startswith("read-")
    ]
    assert len(read_outputs) == 6
    stubbed_reads = [o for o in read_outputs if o.get("note")]
    full_reads = [o for o in read_outputs if "text" in o]
    assert len(stubbed_reads) == 3
    assert len(full_reads) == 3
    assert {o["page"] for o in stubbed_reads} == {0, 1, 2}
    assert {o["page"] for o in full_reads} == {3, 4, 5}
    by_page = {o["page"]: o for o in stubbed_reads}
    for stub in stubbed_reads:
        assert stub["filing_id"] == filing_id
        assert "text omitted to save context" in stub["note"]
        assert "text" not in stub
    assert by_page[0]["recorded_facts"][0]["fact_id"] == "fact-1"
    assert by_page[0]["recorded_facts"][0]["item_key"] == "amount"
    assert len(by_page[0]["recorded_facts"][0]["claim"]) == 100
    assert by_page[1]["recorded_facts"][0]["fact_id"] == "fact-2"
    assert "recorded_facts" not in by_page[2]

    search_outputs = [
        json.loads(item["output"])
        for item in compacted
        if item.get("type") == "function_call_output"
        and item.get("call_id", "").startswith("search-")
    ]
    assert len(search_outputs) == 5
    stubbed_searches = [o for o in search_outputs if o.get("note")]
    full_searches = [o for o in search_outputs if "hits" in o]
    assert len(stubbed_searches) == 2
    assert len(full_searches) == 3


def test_compact_input_size_grows_with_turns_not_pages() -> None:
    """After the keep window, each extra read adds only a stub — not another page."""
    filing_id = "f1"
    page_body = "P" * 8000
    base: list[dict] = [{"type": "message", "role": "user", "content": "start"}]
    sizes: list[int] = []
    for n_reads in range(1, 12):
        transcript = list(base)
        for i in range(n_reads):
            transcript.extend(_read_call(f"r{i}", i, filing_id, page_body))
        compacted = compact_transcript_for_model(transcript)
        sizes.append(len(json.dumps(compacted)))

    # First 3 reads: size grows roughly with full pages.
    growth_early = sizes[2] - sizes[0]
    # Later reads (4→11): only stubs — growth much smaller per turn than a full page.
    growth_late = (sizes[10] - sizes[3]) / 7
    assert growth_early > 10_000
    assert growth_late < 2_000
    # Uncompacted would grow ~8k per page; compacted late growth is stub-sized.
    uncompacted_per_page = len(json.dumps(_read_call("x", 0, filing_id, page_body)))
    assert growth_late < uncompacted_per_page / 3


@pytest.mark.asyncio
async def test_loop_sends_compacted_input_keeps_full_transcript(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    # Four reads → model input should stub the oldest; stored meta keeps all text.
    script = [
        function_call("read_document", {"filing_id": filing.id, "page": 0}),
        function_call("read_document", {"filing_id": filing.id, "page": 0}),
        function_call("read_document", {"filing_id": filing.id, "page": 0}),
        function_call("read_document", {"filing_id": filing.id, "page": 0}),
        plain_text("a"),
        plain_text("b"),
        plain_text("c"),
    ]
    client = FakeModel(script)
    run = await run_event(session, event.id, client, RunLimits(max_turns=40))
    assert run.status == "paused"

    # Fifth model request (after 4 tool turns) is the first plain-text turn —
    # its input should include stubbed early reads.
    assert len(client.requests) >= 5
    req_after_reads = client.requests[4]["input"]
    assert isinstance(req_after_reads, list)
    read_outs = [
        json.loads(item["output"])
        for item in req_after_reads
        if isinstance(item, dict)
        and item.get("type") == "function_call_output"
        and "filing_id" in json.loads(item.get("output") or "{}")
    ]
    # Four read outputs in the compacted input; exactly one stubbed.
    assert len(read_outs) == 4
    assert sum(1 for o in read_outs if o.get("note")) == 1
    assert sum(1 for o in read_outs if "text" in o) == 3

    # Stored transcript on the run still has full page text for every read.
    stored = (run.result or {}).get("transcript") or []
    stored_reads = [
        json.loads(item["output"])
        for item in stored
        if isinstance(item, dict)
        and item.get("type") == "function_call_output"
        and isinstance(item.get("output"), str)
        and '"text"' in item["output"]
    ]
    assert len(stored_reads) == 4
    assert all(len(o.get("text") or "") > 100 for o in stored_reads)
