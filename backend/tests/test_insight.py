"""Insight tests: number grounding, advice guard, gates, cache (all offline).

Scripted fakes stand in for Muse Spark (writer) and Spark Open (verifier);
both speak the ``structured_json`` shape so no network is touched.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.insight.check import (
    InsightAssessmentModel,
    check_headline,
    check_text,
    verify_sentences,
)
from app.insight.facts import build_impact_facts, build_snapshot_facts
from app.insight.generate import (
    generate_impact,
    generate_snapshot,
    input_hash,
)
from app.insight.numbers import collect_numbers, find_violations
from app.llm.client import LLMError
from app.models import Alert, Event, Filing, Issuer

AS_OF = date(2026, 10, 31)
FACTS = {
    "issuer": "Verizon Communications Inc.",
    "metrics": {
        "total_debt": {"value": 160962000000.0},
        "due_24m": {"value": 26836000000.0},
        "leverage": {
            "value": 3.3127,
            "previous_value": 3.5,
            "change_pct": -5.4,
            "change_pct_abs": 5.4,
        },
    },
    "wall": {"buckets": [{"label": "FY2027", "amount": 17267000000.0}]},
    "alerts": [],
}


class FakeModels:
    """One fake for writer + verifier, told apart by the requested schema."""

    def __init__(self, writer=None, verdicts=None, fail_writer=False, fail_check=False):
        self.writer = writer
        self.verdicts = verdicts
        self.fail_writer = fail_writer
        self.fail_check = fail_check
        self.calls: list[str] = []
        self.settings = SimpleNamespace(baseten_api_key="", default_model="fake", open_model="")

    async def structured_json(self, *, provider, model, messages, schema, **kwargs):
        from app.insight.generate import ImpactTextModel, SnapshotTextModel

        name = schema.__name__
        self.calls.append(name)
        if name in {"SnapshotTextModel", "ImpactTextModel"}:
            if self.fail_writer:
                raise LLMError("writer down")
            assert isinstance(self.writer, dict)
            if name == "SnapshotTextModel":
                return SnapshotTextModel.model_validate(self.writer), None
            return ImpactTextModel.model_validate(self.writer), None
        if self.fail_check:
            raise LLMError("verifier down")
        assert isinstance(self.verdicts, list)
        return InsightAssessmentModel.model_validate({"verdicts": self.verdicts}), None


def supported(n):
    return [
        {"sentence_index": i, "status": "supported", "unsupported_phrase": None} for i in range(n)
    ]


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def seed_issuer(session: Session, key="verizon") -> Issuer:
    issuer = Issuer(key=key, name="Verizon Communications Inc.", cik="732712")
    session.add(issuer)
    session.commit()
    return issuer


def seed_snapshot_numbers(session: Session, issuer: Issuer) -> None:
    """CreditMetric + wall rows matching writer_payload() figures."""
    from app.models import CreditMetric, MaturitySchedule

    def row(metric, value, period_end, label, filed, unit="USD"):
        session.add(
            CreditMetric(
                issuer_id=issuer.id,
                metric=metric,
                period_end=period_end,
                fiscal_label=label,
                value=value,
                unit=unit,
                source_tag="T",
                accession="accn",
                filed=filed,
                detail={},
            )
        )

    row("total_debt", Decimal(160962000000), date(2026, 9, 30), "Q3 2026", date(2026, 10, 29))
    row("due_24m", Decimal(26836000000), date(2026, 9, 30), "Q3 2026", date(2026, 10, 29))
    row("leverage", Decimal("3.3127"), date(2026, 9, 30), "Q3 2026", date(2026, 10, 29), unit="x")
    row("leverage", Decimal("3.5"), date(2024, 12, 31), "FY2024", date(2026, 2, 17), unit="x")
    session.add(
        MaturitySchedule(
            issuer_id=issuer.id,
            basis="consolidated",
            as_of=date(2025, 12, 31),
            source_url="https://example.com/10k",
            buckets=[{"label": "FY2027", "period_end": "2027-12-31", "amount": "17267000000"}],
        )
    )
    session.commit()


# --- number matcher ---


def test_exact_and_rounded_numbers_pass():
    # registry mirrors the facts builder: signed change_pct plus its abs form
    registry = [Decimal(160962000000), Decimal("3.3127"), Decimal("-5.4"), Decimal("5.4")]
    assert find_violations("Debt stands at $160,962 million.", registry) == []
    assert find_violations("Debt stands at $161.0 billion.", registry) == []
    assert find_violations("Leverage is 3.31x, down 5.4%.", registry) == []


def test_hallucinated_number_fails():
    registry = [Decimal(160962000000)]
    assert find_violations("Debt stands at $170 billion.", registry) != []


def test_years_forms_and_quarters_skipped():
    registry = [Decimal(160962000000)]
    assert find_violations("In 2026 the 10-K showed debt of $161.0 billion.", registry) == []
    assert find_violations("The Q1 2026 8-K showed debt of $161.0 billion.", registry) == []


def test_bare_small_counts_skipped_but_bare_thousands_checked():
    registry = [Decimal(160962000000)]
    assert find_violations("It owns 3 networks and 24 subsidiaries.", registry) == []
    assert find_violations("Debt of 18,618 is due.", registry) != []  # needs a scale


def test_percent_matches_ratio_and_points():
    registry = [Decimal("0.1675"), Decimal("5.2")]
    assert find_violations("Coverage of 16.8% is thin.", registry) == []
    assert find_violations("Debt is up 5.2% on the year.", registry) == []
    assert find_violations("Debt is up 9.9% on the year.", registry) != []


def test_collect_numbers_registers_leaves():
    assert Decimal("160962000000") in collect_numbers(FACTS)
    assert Decimal("2026") not in collect_numbers({"as_of": "2026-10-31"})


# --- deterministic gates ---


def test_advice_language_blocked_but_substrings_pass():
    assert check_text("Investors should buy the bonds.", FACTS)["advice_ok"] is False
    assert check_text("The upgrade cycle lifted revenue.", FACTS)["advice_ok"] is False
    ok = check_text("Debt stands at $161.0 billion with withholdings steady.", FACTS)
    assert ok["advice_ok"] is True and ok["numbers_ok"] is True


def test_headline_word_limit():
    long = "Verizon refinancing risk is falling fast as maturities extend into next year today"
    assert check_headline(long, FACTS)["headline_ok"] is False
    assert check_headline("Verizon: refinancing risk falling", FACTS)["headline_ok"] is True


# --- verifier ---


@pytest.mark.asyncio
async def test_verifier_marks_unsupported():
    fake = FakeModels(
        verdicts=[
            {
                "sentence_index": 0,
                "status": "unsupported",
                "unsupported_phrase": "$170 billion",
            }
        ]
    )
    out = await verify_sentences(["Debt is $170 billion."], FACTS, client=fake)
    assert out[0]["status"] == "unsupported"
    assert out[0]["phrase"] == "$170 billion"


@pytest.mark.asyncio
async def test_verifier_error_is_fail_closed():
    fake = FakeModels(verdicts=[], fail_check=True)
    out = await verify_sentences(["Debt is $161.0 billion."], FACTS, client=fake)
    assert out[0]["status"] == "error"


# --- generation + cache ---


def writer_payload():
    return {
        "headline": "Verizon: refinancing risk falling",
        "snapshot": [
            "Verizon owes $161.0 billion with $26.8 billion due in 24 months.",
            "Leverage eased to 3.31x, down 5.4% on last year.",
            "Watch the 2027 wall of $17,267 million.",
        ],
    }


@pytest.mark.asyncio
async def test_snapshot_complete_and_cached(session):
    seed_snapshot_numbers(session, seed_issuer(session))
    fake = FakeModels(writer=writer_payload(), verdicts=supported(3))
    first = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert first.status == "complete"
    assert first.headline == "Verizon: refinancing risk falling"
    assert len(first.sentences) == 3
    assert first.dropped == []
    calls = len(fake.calls)
    assert calls == 2  # one writer + one verifier call
    second = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert second.id == first.id and len(fake.calls) == calls  # cache: no new calls


@pytest.mark.asyncio
async def test_bad_number_sentence_dropped_with_refusal(session):
    seed_snapshot_numbers(session, seed_issuer(session))
    payload = writer_payload()
    payload["snapshot"] = payload["snapshot"] + ["Debt will hit $999 billion next year."]
    fake = FakeModels(writer=payload, verdicts=supported(3))
    row = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert row.status == "complete"  # 3 good sentences kept
    assert len(row.sentences) == 3
    assert len(row.dropped) == 1
    assert row.dropped[0]["reason"] == "deterministic gate"
    assert row.dropped[0]["number_violations"] == ["$999 billion"]


@pytest.mark.asyncio
async def test_verifier_rejection_dropped(session):
    seed_snapshot_numbers(session, seed_issuer(session))
    fake = FakeModels(
        writer=writer_payload(),
        verdicts=supported(2)
        + [{"sentence_index": 2, "status": "unsupported", "unsupported_phrase": "$17,267 million"}],
    )
    row = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert len(row.sentences) == 2
    assert row.dropped[0]["reason"] == "independent verifier: unsupported"


@pytest.mark.asyncio
async def test_long_headline_dropped_sentence_kept(session):
    seed_snapshot_numbers(session, seed_issuer(session))
    payload = writer_payload()
    payload["headline"] = " ".join(["word"] * 13)
    fake = FakeModels(writer=payload, verdicts=supported(3))
    row = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert row.headline is None
    assert row.status == "partial"
    assert any(d["reason"] == "headline gate" for d in row.dropped)


@pytest.mark.asyncio
async def test_generation_failure_stored_as_error(session):
    seed_issuer(session)
    fake = FakeModels(writer=writer_payload(), verdicts=[], fail_writer=True)
    row = await generate_snapshot(session, "verizon", AS_OF, client=fake)
    assert row.status == "error"
    assert row.sentences == []


@pytest.mark.asyncio
async def test_impact_sentence_and_cache(session):
    issuer = seed_issuer(session)
    filing = Filing(
        issuer_id=issuer.id,
        form_type="8-K",
        url="https://example.com/x",
        title="8-K",
        filed_at=date(2026, 5, 1),
        body_text="redeemed notes",
    )
    session.add(filing)
    session.flush()
    event = Event(
        replay_key="k1",
        event_type="redemption",
        issuer_id=issuer.id,
        event_date=date(2026, 5, 1),
        source_filing_id=filing.id,
        status="processed",
    )
    session.add(event)
    session.flush()
    alert = Alert(
        issuer_id=issuer.id,
        event_id=event.id,
        title="Redemption",
        summary="Redeemed notes.",
        status="new",
    )
    session.add(alert)
    session.commit()
    facts = build_impact_facts(session, alert.id)
    assert facts["alert_id"] == alert.id
    fake = FakeModels(
        writer={"impact": "Verizon redeemed notes, cutting 2027 maturities."},
        verdicts=supported(1),
    )
    first = await generate_impact(session, alert.id, client=fake)
    assert first.status == "complete"
    assert first.scope == "impact" and first.ref == alert.id
    calls = len(fake.calls)
    second = await generate_impact(session, alert.id, client=fake)
    assert second.id == first.id and len(fake.calls) == calls


def test_input_hash_stable_and_sensitive():
    assert input_hash("snapshot", "", FACTS) == input_hash("snapshot", "", FACTS)
    other = dict(FACTS)
    other["as_of"] = "2026-09-30"
    assert input_hash("snapshot", "", other) != input_hash("snapshot", "", FACTS)


def test_snapshot_facts_only_see_past(session):
    issuer = seed_issuer(session)
    event = Event(
        replay_key="k2",
        event_type="filing",
        issuer_id=issuer.id,
        event_date=date(2026, 11, 15),
        status="pending",
    )
    session.add(event)
    session.flush()
    alert = Alert(
        issuer_id=issuer.id, event_id=event.id, title="Future", summary="Not yet.", status="new"
    )
    session.add(alert)
    session.commit()
    facts = build_snapshot_facts(session, "verizon", AS_OF)
    assert facts["alerts"] == []
    _ = json.dumps(facts, default=str)  # facts must be JSON-serializable
