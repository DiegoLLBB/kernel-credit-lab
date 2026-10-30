"""Claims check claims_v2: regression cases from the 29 Oct replay calibration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.claims import PROMPT_VERSION, ClaimsResult, claims_check, load_claims_prompt
from app.agent.fake import FakeClaimsClient
from app.db import Base
from app.models.trace import AgentRun

# Four phrases that claims_v1 wrongly refused in the full replay; v2 must treat as supported.
SUPPORTED_CASES = [
    {
        "id": "verizon_principal_amount_outstanding",
        "statement": (
            "Principal Amount Outstanding for the notes accepted in the tender is as shown."
        ),
        "quotes": [
            "Principal Amount Outstanding Accepted $234,260,000 for the 6.860% Debentures due 2028"
        ],
        "why": "table heading / defined term from the document",
    },
    {
        "id": "boeing_undrawn_backup",
        "statement": (
            "Boeing maintains undrawn backup facilities under its revolving credit agreements."
        ),
        "quotes": [
            "As of the date hereof, there were no borrowings outstanding under the Company's "
            "backup revolving credit facilities"
        ],
        "why": "plain description of undrawn when quote says no borrowings outstanding",
    },
    {
        "id": "amazon_entered_agreement",
        "statement": "Amazon entered the agreement for the new notes offering.",
        "quotes": [
            "Amazon.com, Inc. entered into an underwriting agreement dated March 13, 2026 "
            "with respect to the notes"
        ],
        "why": "paraphrase of the quoted entry into the agreement",
    },
    {
        "id": "duke_energy_issuer_name",
        "statement": "Duke Energy priced $1.3bn of 3.000% Convertible Senior Notes due 2029.",
        "quotes": [
            "Duke Energy Corporation announced the pricing of its offering of $1.3 billion "
            "aggregate principal amount of its 3.000% convertible senior notes due 2029"
        ],
        "why": "issuer name as appearing in the filing",
    },
    {
        "id": "verizon_waterfall_notes_label",
        "statement": (
            "Waterfall Notes are included in the tender; the Waterfall Cap limits aggregate "
            "purchase price."
        ),
        "quotes": [
            'outstanding series of debt securities listed in Table 2 below (the "Waterfall '
            'Notes" and, together with the Any and All Notes, the "Notes"); Verizon is '
            "offering to accept for purchase validly tendered Waterfall Notes subject to a "
            '$1.25 billion cap (the "Waterfall Cap")'
        ],
        "why": ("defined term / series name from the document (Verizon tender launch needs_input)"),
    },
]

# Real overstatements that must still fail.
OVERSTATED_CASES = [
    {
        "id": "settled_when_expected",
        "statement": "The notes closed and settled on March 12, 2026.",
        "quotes": ["The sale of the convertible notes is expected to close on March 12, 2026"],
        "unsupported_phrase": "closed and settled",
        "why": "document only says expected to close — settled is a later stage",
    },
    {
        "id": "amount_inflated",
        "statement": "The issuer redeemed $2,500,000,000 of notes due 2028.",
        "quotes": ["will redeem, in whole, $1,250,000,000 aggregate principal amount"],
        "unsupported_phrase": "$2,500,000,000",
        "why": "number larger than the cited quote",
    },
]


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_claims_prompt_is_v2() -> None:
    assert PROMPT_VERSION == "claims_v2"
    text = load_claims_prompt()
    assert "claims_v2" in text
    assert "add a fact, number, date or certainty" in text
    assert "Principal Amount Outstanding" in text or "table headings" in text
    assert "Waterfall Notes" in text
    assert "names and defined terms" in text.lower()
    assert "undrawn" in text.lower()
    # v1 kept on disk
    v1 = (Path(__file__).resolve().parents[1] / "app/agent/prompts/claims_v1.md").read_text()
    assert "claims_v1" in v1
    assert "add a fact, number, date or certainty" not in v1
    assert "Waterfall Notes" not in v1


def _supported_script() -> dict:
    return {
        "summary": {"status": "supported", "unsupported_phrase": None},
        "findings": [
            {
                "finding_index": 0,
                "item_key": "instrument",
                "status": "supported",
                "unsupported_phrase": None,
            }
        ],
    }


def _overstated_script(phrase: str) -> dict:
    return {
        "summary": {"status": "overstated", "unsupported_phrase": phrase},
        "findings": [
            {
                "finding_index": 0,
                "item_key": "instrument",
                "status": "overstated",
                "unsupported_phrase": phrase,
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SUPPORTED_CASES, ids=[c["id"] for c in SUPPORTED_CASES])
async def test_claims_v2_regression_supported(session: Session, case: dict) -> None:
    """Replay refusals that should be supported under claims_v2 (scripted verifier)."""
    run = AgentRun(
        question="claims regression",
        workflow="event_monitor",
        mode="standard",
        status="running",
        state="AGENT",
        metrics_outputs=[],
    )
    session.add(run)
    session.flush()
    fact = SimpleNamespace(id="fact-supported", exact_quote=case["quotes"][0])

    client = FakeClaimsClient([_supported_script()])
    result = await claims_check(
        session,
        run,
        case["statement"],
        [
            {
                "item_key": "instrument",
                "statement": case["statement"],
                "citations": [fact.id],
            }
        ],
        client=client,
        facts_by_id={fact.id: fact},  # type: ignore[dict-item]
    )
    assert isinstance(result, ClaimsResult)
    assert result.status == "pass", (case["id"], result.problems)
    assert result.summary is not None
    assert result.summary["status"] == "supported"
    # Payload must carry the quote so a live model would see the label/paraphrase evidence.
    assert client.requests
    body = client.requests[-1]["messages"][-1]["content"]
    assert case["quotes"][0][:40] in body


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OVERSTATED_CASES, ids=[c["id"] for c in OVERSTATED_CASES])
async def test_claims_v2_regression_overstated(session: Session, case: dict) -> None:
    run = AgentRun(
        question="claims overstated",
        workflow="event_monitor",
        mode="standard",
        status="running",
        state="AGENT",
        metrics_outputs=[],
    )
    session.add(run)
    session.flush()
    fact = SimpleNamespace(id="fact-over", exact_quote=case["quotes"][0])

    client = FakeClaimsClient([_overstated_script(case["unsupported_phrase"])])
    result = await claims_check(
        session,
        run,
        case["statement"],
        [
            {
                "item_key": "instrument",
                "statement": case["statement"],
                "citations": [fact.id],
            }
        ],
        client=client,
        facts_by_id={fact.id: fact},  # type: ignore[dict-item]
    )
    assert result.status == "fail"
    assert any("overstated" in p for p in result.problems)
    assert case["unsupported_phrase"] in " ".join(result.problems)
