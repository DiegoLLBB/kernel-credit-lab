"""Agent tools (§9.3): Verizon redemption happy path and refusal cases."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.claims import ClaimsResult
from app.agent.fake import FakeClaimsClient
from app.agent.materiality import assess_materiality, is_missing_from_bond_list
from app.agent.normalize import (
    AMOUNT_FORMAT_HINT,
    RATE_FORMAT_HINT,
    amount_in_quote,
    coerce_amount,
    date_in_quote,
    detect_amount_scale,
    extract_dates,
    extract_month_years,
    extract_numbers,
    instrument_identified_in_quote,
    parse_date_with_precision,
    parse_rate,
    quote_has_context,
)
from app.agent.run import start_agent_run
from app.agent.text import PAGE_CHARS, html_to_text, page_count
from app.agent.tools import alert as alert_mod
from app.agent.tools import (
    compute_metrics,
    get_issuer_debt,
    list_documents,
    propose_change,
    read_document,
    record_fact,
    request_input,
    search_document,
    set_checklist,
)
from app.db import Base
from app.llm.client import LLMError
from app.models import (
    Alert,
    AuditEntry,
    ChecklistItem,
    Event,
    EventFiling,
    Filing,
    Instrument,
    Issuer,
    MaturitySchedule,
    ReplayExpectation,
    StateChange,
)
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"
REDEMPTION_HTML = (
    Path(__file__).resolve().parent / "fixtures" / "newsroom" / "verizon_redemption_2026-08-20.html"
)


def submit_alert(*args: object, **kwargs: object) -> dict:
    """Sync test helper — tools never create their own event loop."""
    return asyncio.run(alert_mod.submit_alert(*args, **kwargs))  # type: ignore[arg-type]


def _vz_quotes() -> dict[str, str]:
    """Exact substrings from the recorded Verizon redemption page (html_to_text)."""
    text = html_to_text(REDEMPTION_HTML.read_text(encoding="utf-8", errors="replace"))

    def take(start: str, n: int) -> str:
        i = text.find(start)
        assert i >= 0, start
        return text[i : i + n]

    return {
        "instrument": take("At a glance Verizon today announced", 120),
        "amount": take("4.329% Notes due 2028 (the", 115),
        "redemption_date": take("today announced that it will redeem, in whole", 105),
        "price_basis": take("at the Treasury Rate (as defined", 130),
        "conditions": take("NEW YORK, NY", 160),
        "maturity_effect": take("At a glance Verizon today announced", 120),
        "counter_evidence": take("NEW YORK, NY", 160),
    }


REDEMPTION_QUOTES = _vz_quotes()
Q_INSTRUMENT = REDEMPTION_QUOTES["instrument"]
Q_AMOUNT = REDEMPTION_QUOTES["amount"]
Q_DATE = REDEMPTION_QUOTES["redemption_date"]


def _claims_supported(findings: list[dict] | None = None) -> FakeClaimsClient:
    """Fake independent verifier that marks summary and every finding supported."""
    items = findings or []
    return FakeClaimsClient(
        [
            {
                "summary": {"status": "supported", "unsupported_phrase": None},
                "findings": [
                    {
                        "finding_index": i,
                        "item_key": f.get("item_key"),
                        "status": "supported",
                        "unsupported_phrase": None,
                    }
                    for i, f in enumerate(items)
                ],
            }
        ]
    )


REDEMPTION_ITEMS = [
    {
        "key": "instrument",
        "question": "Which notes are being redeemed?",
        "evidence_needed": "Title / CUSIP of the notes",
    },
    {
        "key": "amount",
        "question": "What principal is outstanding / redeemed?",
        "evidence_needed": "Principal amount",
    },
    {
        "key": "redemption_date",
        "question": "When is the redemption date?",
        "evidence_needed": "Redemption date",
    },
    {
        "key": "price_basis",
        "question": "How is the redemption price calculated?",
        "evidence_needed": "Price formula / make-whole",
    },
    {
        "key": "conditions",
        "question": "Are there conditions to the redemption?",
        "evidence_needed": "Any conditionality",
    },
    {
        "key": "maturity_effect",
        "question": "How does this change the maturity wall?",
        "evidence_needed": "Removal of 2028 notes",
    },
    {
        "key": "counter_evidence",
        "question": "Is any part of the redemption conditional or partial?",
        "evidence_needed": "Language that the redemption is in whole",
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


def _record_all_facts(session: Session, run, filing: Filing) -> dict[str, str]:
    fact_ids: dict[str, str] = {}
    for key, quote in REDEMPTION_QUOTES.items():
        assert quote in (filing.body_text or ""), f"fixture missing {key}"
        r = record_fact(session, run, key, filing.id, quote, f"claim for {key}")
        assert r["ok"], r
        fact_ids[key] = r["fact_id"]
    return fact_ids


def _happy_path(session: Session, event: Event, filing: Filing) -> dict:
    run = start_agent_run(session, event)
    session.commit()

    result = set_checklist(session, run, "redemption", REDEMPTION_ITEMS)
    assert result["ok"] is True
    assert run.coverage_score is not None
    assert "coverage_score" not in result
    assert "Flagship alert" not in str(result)

    docs = list_documents(session, run)
    assert docs["ok"] and len(docs["documents"]) == 1
    assert read_document(session, run, filing.id, page=0)["ok"]

    fact_ids = _record_all_facts(session, run, filing)

    change = propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )
    assert change["ok"], change

    metrics = compute_metrics(session, run)
    assert metrics["ok"], metrics
    compact = metrics["metrics"]
    assert compact["id"]
    assert "FY2028" in compact["issuer"]["changed_buckets"]
    assert (
        compact["issuer"]["totals_2026_2029"]["before"]
        != compact["issuer"]["totals_2026_2029"]["after"]
    )
    # Full walls stay on the run for alerts / dashboard.
    full = (run.metrics_outputs or [])[-1]
    assert full["id"] == compact["id"]
    before = Decimal(full["before"]["issuer"]["buckets"]["FY2028"]["total_outstanding"])
    after = Decimal(full["after"]["issuer"]["buckets"]["FY2028"]["total_outstanding"])
    assert before - after == Decimal("1250000000")
    assert compact["issuer"]["changed_buckets"]["FY2028"] == {
        "before": str(before),
        "after": str(after),
    }

    findings = [
        {
            "item_key": key,
            "statement": f"Settled {key} with amount $1,250,000,000 on September 21, 2026.",
            "citations": [fact_ids[key]],
        }
        for key in fact_ids
    ]
    alert = submit_alert(
        session,
        run,
        summary=(
            "Verizon will redeem all $1,250,000,000 of its 4.329% Notes due 2028 "
            "on September 21, 2026, cutting the FY2028 wall by $1,250,000,000."
        ),
        findings=findings,
        material=True,
        claims_client=_claims_supported(findings),
    )
    assert alert["ok"], alert
    assert alert["status"] == "alerted"
    assert alert["material"] is True
    assert alert["claims_check"] == "pass"
    stored = session.scalars(select(Alert).where(Alert.event_id == event.id)).one()
    assert stored.claims_check is not None
    assert stored.claims_check["status"] == "pass"
    session.commit()
    return {"run": run, "alert": alert, "before": before, "after": after, "fact_ids": fact_ids}


def test_amount_matches_any_number_in_quote() -> None:
    quote = (
        "The redemption date is September 21, 2026 for the 4.329% Notes due 2028, "
        "of which $1,250,000,000 is outstanding"
    )
    nums = extract_numbers(quote)
    assert Decimal("1250000000") in nums
    assert amount_in_quote("1250000000", quote)
    assert amount_in_quote(Decimal("1250000000"), quote)


def test_amount_scale_millions_accepts_table_units() -> None:
    amount_quote = "Term loan 1,000 under the unsecured facility due March 2027"
    scale_quote = "Long-term debt outstanding (in millions) as of March 31, 2026 for the registrant"
    assert detect_amount_scale(scale_quote) == Decimal("1000000")
    assert amount_in_quote("1000000000", amount_quote, scale=detect_amount_scale(scale_quote))
    assert not amount_in_quote("1000000000", amount_quote)


def test_date_matches_any_date_in_quote() -> None:
    quote = (
        "On August 20, 2026, Verizon announced that it will redeem the notes on September 21, 2026"
    )
    assert extract_dates(quote) == [date(2026, 8, 20), date(2026, 9, 21)]
    assert date_in_quote("2026-09-21", quote)
    assert date_in_quote(date(2026, 9, 21), quote)


def test_extract_dates_parses_numeric_slash_dates() -> None:
    assert extract_dates("redemption on 09/21/2026 at par") == [date(2026, 9, 21)]
    assert date_in_quote("2026-09-21", "Notice dated 09/21/2026 for the notes")


def test_month_precision_date_in_quote_and_parse() -> None:
    quote = "unsecured term loan facility due March 2027 under the credit agreement"
    assert extract_month_years(quote) == [(2027, 3)]
    assert date_in_quote("2027-03-01", quote)
    assert date_in_quote("2027-03-15", quote)
    assert date_in_quote("2027-03", quote)
    d, prec = parse_date_with_precision("March 2027")
    assert d == date(2027, 3, 1)
    assert prec == "month"
    d2, prec2 = parse_date_with_precision("2027-03")
    assert d2 == date(2027, 3, 1) and prec2 == "month"


def test_instrument_requires_coupon_and_year() -> None:
    assert instrument_identified_in_quote(
        quote="the 4.329% Notes due 2028 will be redeemed in full next month",
        name="4.329% Notes due 2028",
        cusip="92343VER1",
        coupon=Decimal("4.329"),
        maturity_year=2028,
    )
    assert not instrument_identified_in_quote(
        quote="the issuer cited a 4.329% coupon without naming the year",
        name="4.329% Notes due 2028",
        cusip="92343VER1",
        coupon=Decimal("4.329"),
        maturity_year=2028,
    )


def test_quote_context_minimum() -> None:
    assert not quote_has_context("$1,250,000,000")
    assert not quote_has_context("4.329%")
    assert quote_has_context(Q_AMOUNT)


def test_verizon_redemption_produces_alert_and_fy2028_drop(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, bond = verizon_event
    out = _happy_path(session, event, filing)
    assert out["before"] - out["after"] == Decimal("1250000000")
    alert = session.scalars(select(Alert).where(Alert.event_id == event.id)).one()
    assert alert.status == "new"
    session.refresh(bond)
    assert bond.status == "redeemed"
    applied = session.scalars(
        select(StateChange).where(
            StateChange.run_id == out["run"].id, StateChange.status == "applied"
        )
    ).all()
    assert applied
    assert any(e.actor == "verification" for e in session.scalars(select(AuditEntry)).all())


def test_get_issuer_debt_lists_verizon_4329_active(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    run = start_agent_run(session, event)
    result = get_issuer_debt(session, run)
    assert result["ok"] is True
    assert result["as_of"] == "2026-08-20"
    by_key = {row["key"]: row for row in result["instruments"]}
    note = by_key["vz_4329_2028"]
    assert note["status"] == "active"
    assert Decimal(note["amount_outstanding"]) == Decimal("1250000000")
    assert "FY2028" in (result["maturity_wall"]["buckets"] or {})


def test_coverage_score_stored_not_in_feedback(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    run = start_agent_run(session, event)
    incomplete = [i for i in REDEMPTION_ITEMS if i["key"] != "price_basis"]
    refused = set_checklist(session, run, "redemption", incomplete)
    assert refused["ok"] is False
    assert "price_basis" in (refused.get("problems") or [])
    assert run.coverage_score is not None
    feedback = str(refused)
    assert "Flagship alert" not in feedback
    assert "expectation_key" not in refused


def test_record_fact_refuses_paraphrase(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    bad = record_fact(
        session,
        run,
        item_key="instrument",
        filing_id=filing.id,
        quote=(
            "Verizon is calling its four-point-three-two-nine percent notes "
            "due twenty twenty-eight in a make-whole redemption"
        ),
        claim="paraphrase",
    )
    assert bad["ok"] is False
    assert "quote not found" in bad["reason"]


def test_record_fact_refuses_too_short(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    bad = record_fact(session, run, "amount", filing.id, "$1,250,000,000", "lone amount")
    assert bad["ok"] is False
    assert "too short" in bad["reason"]


def test_parse_rate_and_amount_formats() -> None:
    assert parse_rate("3.000%") == Decimal("3.000")
    assert parse_rate("4.522") == Decimal("4.522")
    assert parse_rate(" 3.0 % ") == Decimal("3.0")
    assert parse_rate("not-a-rate") is None
    assert coerce_amount("$1.3 billion") == Decimal("1300000000")
    assert coerce_amount("nope") is None


def test_propose_change_normalizes_coupon_percent(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    """Agent may pass ``3.000%``; proposed_diff stores a plain Decimal string."""
    event, filing, _bond = verizon_event
    amount_quote = (
        "Issuer priced a new 3.000% senior note due 2031 with principal amount "
        "of $500,000,000 as described in the offering circular"
    )
    maturity_quote = (
        "The 3.000% senior notes mature on May 15, 2031 under the indenture "
        "governing the newly issued securities described herein"
    )
    filing.body_text = f"{amount_quote}\n{maturity_quote}\n{filing.body_text or ''}"
    session.flush()

    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    amt = record_fact(session, run, "amount", filing.id, amount_quote, "face")
    mat = record_fact(session, run, "maturity_effect", filing.id, maturity_quote, "mat")
    assert amt["ok"] and mat["ok"]

    ok_change = propose_change(
        session,
        run,
        {
            "action": "add_instrument",
            "instrument_key": "test_3000_2031",
            "effective_date": "2026-05-01",
            "name": "3.000% Senior Notes due 2031",
            "coupon": "3.000%",
            "maturity": "2031-05-15",
            "issue_date": "2026-05-01",
            "amount": "500000000",
            "currency": "USD",
            "instrument_type": "bond",
            "evidence": {
                "amount": amt["fact_id"],
                "maturity": mat["fact_id"],
            },
        },
    )
    assert ok_change["ok"], ok_change
    row = session.get(StateChange, ok_change["state_change_id"])
    assert row is not None
    assert row.proposed_diff["coupon"] == "3.000"
    # compute_metrics must accept the stored coupon (no ConversionSyntax).
    assert compute_metrics(session, run)["ok"]


def test_propose_change_refuses_invalid_coupon_and_amount(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    amt = record_fact(session, run, "amount", filing.id, Q_AMOUNT, "amount")
    assert amt["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "add_instrument",
            "instrument_key": "bad_coupon_loan",
            "effective_date": "2026-05-01",
            "name": "Bad coupon loan",
            "coupon": "three percent",
            "maturity": "2031-05-15",
            "issue_date": "2026-05-01",
            "amount": "not-money",
            "currency": "USD",
            "instrument_type": "loan",
            "evidence": {"amount": amt["fact_id"]},
        },
    )
    assert bad["ok"] is False
    assert any("coupon=" in p and RATE_FORMAT_HINT in p for p in bad["problems"])
    assert any("amount_outstanding=" in p and AMOUNT_FORMAT_HINT in p for p in bad["problems"])


def test_propose_change_refuses_coupon_without_year(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    # Plant a long quote that has the coupon but not the maturity year.
    planted = (
        "Analysts noted the 4.329% coupon on the Verizon securities without "
        "stating which maturity year applies to that coupon rate alone"
    )
    filing.body_text = (filing.body_text or "") + " " + planted
    session.flush()
    assert read_document(session, run, filing.id, page=0)["ok"]
    inst = record_fact(session, run, "instrument", filing.id, planted, "coupon only")
    date_f = record_fact(session, run, "redemption_date", filing.id, Q_DATE, "date")
    assert inst["ok"] and date_f["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": inst["fact_id"],
                "effective_date": date_f["fact_id"],
            },
        },
    )
    assert bad["ok"] is False
    assert any("instrument not identified" in p for p in bad["problems"])


def test_propose_change_amount_from_realistic_multi_value_quote(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    multi = (
        "The redemption date is September 21, 2026 for the 4.329% Notes due 2028, "
        "of which $1,250,000,000 is outstanding"
    )
    filing.body_text = (filing.body_text or "") + " " + multi
    session.flush()
    assert read_document(session, run, filing.id, page=0)["ok"]
    inst = record_fact(session, run, "instrument", filing.id, multi, "inst")
    amt = record_fact(session, run, "amount", filing.id, multi, "amt")
    assert inst["ok"] and amt["ok"]
    ok_change = propose_change(
        session,
        run,
        {
            "action": "reduce_outstanding",
            "instrument_key": bond.key,
            "effective_date": "2026-09-21",
            "reduce_by": "1250000000",
            "evidence": {"instrument": inst["fact_id"], "amount": amt["fact_id"]},
        },
    )
    assert ok_change["ok"], ok_change


def test_propose_change_refuses_amount_not_in_quote(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    inst = record_fact(session, run, "instrument", filing.id, Q_INSTRUMENT, "instrument")
    amt = record_fact(session, run, "amount", filing.id, Q_AMOUNT, "amount is 1.25bn")
    assert inst["ok"] and amt["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "reduce_outstanding",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "reduce_by": "500000000",
            "evidence": {
                "instrument": inst["fact_id"],
                "amount": amt["fact_id"],
            },
        },
    )
    assert bad["ok"] is False
    assert any("not found in cited quote" in p for p in bad["problems"])
    # Citation was present — no actionable fact list (only for missing/unknown citations).
    assert "recorded_facts" not in bad


def test_propose_change_missing_citation_lists_recorded_facts(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    amt = record_fact(session, run, "amount", filing.id, Q_AMOUNT, "DEC term loan is $1bn")
    assert amt["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "add_instrument",
            "instrument_key": "dec_tl_2027",
            "effective_date": "2026-03-13",
            "name": "Unsecured Term Loan due 2027",
            "coupon": "4.79",
            "maturity": "2027-03-12",
            "issue_date": "2026-03-13",
            "amount": "1000000000",
            "currency": "USD",
            "instrument_type": "loan",
            "rate_type": "floating",
            "evidence": {},
        },
    )
    assert bad["ok"] is False
    assert any("missing or unknown fact citation" in p for p in bad["problems"])
    facts = bad.get("recorded_facts") or []
    assert any(f.get("fact_id") == amt["fact_id"] for f in facts)
    listed = next(f for f in facts if f["fact_id"] == amt["fact_id"])
    assert listed["item_key"] == "amount"
    assert "1,250" in listed["quote"] or "4.329" in listed["quote"]
    assert len(listed["quote"]) <= 120
    needed = bad.get("evidence_fields_needed") or []
    assert "amount" in needed
    assert "maturity" in needed
    assert "name" in needed
    assert "coupon_or_rate" in needed


def test_propose_change_amount_scale_and_month_maturity(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    """Table units + month-only maturity (Duke 10-Q style) accepted on add_instrument."""
    event, filing, _bond = verizon_event
    amount_quote = (
        "Duke Energy Carolinas entered an unsecured Term loan facility of 1,000 "
        "due March 2027 under its revolving credit agreement disclosures"
    )
    scale_quote = (
        "The following consolidated schedule of long-term debt outstanding "
        "(in millions) as of March 31, 2026 should be read with the notes"
    )
    maturity_quote = (
        "Carolinas has an unsecured term loan facility due March 2027 that was "
        "drawn in full during the first quarter of the year"
    )
    # Prepend so the quotes stay on page 0 even if the fixture is near PAGE_CHARS.
    filing.body_text = f"{amount_quote}\n{scale_quote}\n{maturity_quote}\n{filing.body_text or ''}"
    session.flush()

    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    amt = record_fact(session, run, "amount", filing.id, amount_quote, "table shows 1,000")
    scale = record_fact(session, run, "amount", filing.id, scale_quote, "amounts in millions")
    mat = record_fact(session, run, "maturity_effect", filing.id, maturity_quote, "due March 2027")
    assert amt["ok"] and scale["ok"] and mat["ok"]

    ok_change = propose_change(
        session,
        run,
        {
            "action": "add_instrument",
            "instrument_key": "dec_tl_2027_test",
            "effective_date": "2026-03-13",
            "name": "Unsecured Term Loan due March 2027",
            "coupon": "4.79",
            "maturity": "2027-03",
            "issue_date": "2026-03-13",
            "amount": "1000000000",
            "currency": "USD",
            "instrument_type": "loan",
            "rate_type": "floating",
            "evidence": {
                "amount": amt["fact_id"],
                "amount_scale": scale["fact_id"],
                "maturity": mat["fact_id"],
            },
        },
    )
    assert ok_change["ok"], ok_change
    row = session.get(StateChange, ok_change["state_change_id"])
    assert row is not None
    assert row.proposed_diff["maturity"] == "2027-03-01"
    assert row.proposed_diff["maturity_precision"] == "month"
    assert row.proposed_diff["amount_outstanding"] == "1000000000"

    # Apply via alert path is heavy; check get_issuer_debt after manual insert mirroring apply.
    session.add(
        Instrument(
            key="dec_tl_shown",
            issuer_id=event.issuer_id,
            name="Unsecured Term Loan due March 2027",
            coupon=Decimal("4.79"),
            maturity=date(2027, 3, 1),
            maturity_precision="month",
            issue_date=date(2026, 3, 13),
            amount_outstanding=Decimal("1000000000"),
            currency="USD",
            instrument_type="loan",
            rate_type="floating",
            status="active",
        )
    )
    session.flush()
    debt = get_issuer_debt(session, run)
    by_key = {row["key"]: row for row in debt["instruments"]}
    assert by_key["dec_tl_shown"]["maturity_precision"] == "month"
    assert by_key["dec_tl_shown"]["maturity"] == "2027-03-01"


def test_propose_change_maturity_message_shows_parse_failure(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    amt = record_fact(session, run, "amount", filing.id, Q_AMOUNT, "amount")
    assert amt["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "add_instrument",
            "instrument_key": "bad_mat_loan",
            "effective_date": "2026-03-13",
            "name": "Mystery Loan",
            "coupon": "4.79",
            "maturity": "sometime in fiscal 2027",
            "issue_date": "2026-03-13",
            "amount": "1250000000",
            "currency": "USD",
            "evidence": {"amount": amt["fact_id"]},
        },
    )
    assert bad["ok"] is False
    joined = "; ".join(bad.get("problems") or [])
    assert "maturity must come after issue_date" in joined
    assert "could not parse 'sometime in fiscal 2027'" in joined
    assert "issue_date=2026-03-13" in joined


def test_propose_change_refuses_reduction_above_outstanding(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    oversized = (
        "A hypothetical schedule listed Principal Amount Outstanding "
        "$9,999,000,000 for comparison purposes only in this fixture"
    )
    filing.body_text = (filing.body_text or "") + " " + oversized
    session.flush()
    assert read_document(session, run, filing.id, page=0)["ok"]
    inst = record_fact(session, run, "instrument", filing.id, Q_INSTRUMENT, "inst")
    amt = record_fact(session, run, "amount", filing.id, oversized, "too much")
    assert inst["ok"] and amt["ok"]
    bad = propose_change(
        session,
        run,
        {
            "action": "reduce_outstanding",
            "instrument_key": bond.key,
            "effective_date": "2026-09-21",
            "reduce_by": "9999000000",
            "evidence": {"instrument": inst["fact_id"], "amount": amt["fact_id"]},
        },
    )
    assert bad["ok"] is False
    assert any("exceeds outstanding" in p for p in bad["problems"])


def test_submit_alert_refuses_number_not_in_outputs(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    assert propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )["ok"]
    assert compute_metrics(session, run)["ok"]
    bad = submit_alert(
        session,
        run,
        summary="This invents a $777,000,000 figure that appears nowhere in the record.",
        findings=[
            {
                "item_key": "amount",
                "statement": "Amount is fine at $1,250,000,000.",
                "citations": [fact_ids["amount"]],
            }
        ],
        material=True,
    )
    assert bad["ok"] is False
    assert any("777" in p or "appears in no" in p for p in bad["problems"])


def test_request_input_refuses_before_all_pages_read(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, _filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    bad = request_input(session, run, "amount", "What is the amount?")
    assert bad["ok"] is False
    assert "not every page" in bad["reason"]


def test_search_document_hits_and_does_not_count_as_read(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    result = search_document(session, run, filing.id, "4.329%")
    assert result["ok"] is True
    assert result["hit_count"] >= 1
    assert len(result["hits"]) <= 10
    hit = result["hits"][0]
    assert "page" in hit and "snippet" in hit
    assert "4.329%" in hit["snippet"] or "4.329" in hit["snippet"]
    assert not (run.pages_read or {}).get(filing.id)
    audits = session.scalars(
        select(AuditEntry).where(
            AuditEntry.run_id == run.id, AuditEntry.action == "search_document"
        )
    ).all()
    assert len(audits) >= 1
    assert audits[0].detail and audits[0].detail.get("query") == "4.329%"


def test_request_input_allows_long_doc_after_three_empty_searches(session: Session) -> None:
    load_seed(session, SEED_PATH)
    session.commit()
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    # More than 20 pages of filler with no matching terms for empty searches.
    body = ("AAAA " * (PAGE_CHARS // 5 + 1)) * 22
    assert page_count(body) > 20
    filing = Filing(
        issuer_id=verizon.id,
        form_type="10-Q",
        url="https://www.sec.gov/Archives/edgar/data/732712/fake/long.htm",
        filed_at=date(2026, 5, 1),
        title="Long 10-Q",
        body_text=body,
        content_hash="long",
    )
    session.add(filing)
    session.flush()
    event = Event(
        replay_key="vz_long_doc",
        event_type="10-Q",
        issuer_id=verizon.id,
        event_date=date(2026, 5, 1),
        source_filing_id=filing.id,
        summary="Verizon long filing",
        status="pending",
    )
    session.add(event)
    session.flush()
    session.add(EventFiling(event_id=event.id, filing_id=filing.id))
    session.commit()

    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert request_input(session, run, "amount", "missing?")["ok"] is False

    for q in ("zzzz-one", "zzzz-two", "zzzz-three"):
        r = search_document(session, run, filing.id, q)
        assert r["ok"] and r["nothing_new"] is True and r["hit_count"] == 0

    assert not (run.pages_read or {}).get(filing.id)
    ok_req = request_input(session, run, "amount", "Still missing after searches?")
    assert ok_req["ok"] is True
    assert run.status == "needs_input"


def test_verizon_hybrids_logged_not_material_by_bucket_alone(session: Session) -> None:
    """Long-dated new issuance: per-bucket rules only — no missing-debt flag."""
    load_seed(session, SEED_PATH)
    session.commit()
    verizon = session.scalars(select(Issuer).where(Issuer.key == "verizon")).one()
    before = {
        "buckets": {
            "FY2028": {"total_outstanding": "13032000000"},
            "FY2026": {"total_outstanding": "1000"},
            "FY2027": {"total_outstanding": "1000"},
            "FY2029": {"total_outstanding": "1000"},
        }
    }
    after = {
        "buckets": {
            "FY2028": {"total_outstanding": "13032000000"},
            "FY2026": {"total_outstanding": "1000"},
            "FY2027": {"total_outstanding": "1000"},
            "FY2029": {"total_outstanding": "1000"},
        }
    }
    # Fresh hybrid: issue_date on discovery day → not missing-from-list.
    assert not is_missing_from_bond_list(
        issue_date=date(2026, 2, 23), available_from=date(2026, 2, 23)
    )
    result = assess_materiality(before=before, after=after, new_debt_not_in_list=False)
    assert result["material"] is False

    event = Event(
        replay_key="vz_hybrid_feb",
        event_type="8-K",
        issuer_id=verizon.id,
        event_date=date(2026, 2, 23),
        summary="Verizon hybrid",
        status="pending",
    )
    session.add(event)
    session.flush()
    session.add(
        ReplayExpectation(
            key="vz_hybrid_feb",
            issuer_key="verizon",
            event_type="new_issuance",
            event_date=date(2026, 2, 23),
            available_from=date(2026, 2, 23),
            title="Verizon hybrids due 2056",
            sources=[],
            expected_changes=[{"action": "log_only"}],
            expected_alert=False,
            event_id=event.id,
        )
    )
    run = start_agent_run(session, event)
    assert set_checklist(
        session,
        run,
        "other",
        [
            {
                "key": "subject",
                "question": "What was issued?",
                "evidence_needed": "hybrid terms",
            },
            {
                "key": "effect",
                "question": "Wall impact?",
                "evidence_needed": "none inside horizon",
            },
            {
                "key": "counter_evidence",
                "question": "Any near-term maturity?",
                "evidence_needed": "2056 only",
            },
        ],
    )["ok"]
    for item in session.scalars(select(ChecklistItem).where(ChecklistItem.run_id == run.id)).all():
        item.status = "escalated"
    wall = {
        "buckets": {
            lbl: {"total_outstanding": "0"} for lbl in ("FY2026", "FY2027", "FY2028", "FY2029")
        }
    }
    run.metrics_outputs = [
        {
            "id": "hybrid-metrics",
            "before": {"issuer": wall},
            "after": {"issuer": wall},
        }
    ]
    session.flush()
    out = submit_alert(
        session,
        run,
        summary="Verizon issued long-dated hybrids due 2056; no 2026-2029 wall change.",
        findings=[],
        material=False,
        claims_client=_claims_supported([]),
    )
    assert out["ok"], out
    assert out["status"] == "logged"
    assert out["material"] is False
    assert session.scalars(select(Alert).where(Alert.event_id == event.id)).first() is None


def test_duke_carolinas_term_loan_missing_from_list_is_material(session: Session) -> None:
    """Issued 13 Mar, found 5 May → missing from bond list (always material)."""
    load_seed(session, SEED_PATH)
    session.commit()
    assert is_missing_from_bond_list(issue_date=date(2026, 3, 13), available_from=date(2026, 5, 5))
    # Even with tiny bucket deltas, the missing-debt flag alone is material.
    wall = {
        "buckets": {
            lbl: {"total_outstanding": "67000000"}
            for lbl in ("FY2026", "FY2027", "FY2028", "FY2029")
        }
    }
    result = assess_materiality(before=wall, after=wall, new_debt_not_in_list=True)
    assert result["material"] is True
    assert "debt appears that was not in the bond list" in result["reasons"]

    carolinas = session.scalars(select(Issuer).where(Issuer.key == "duke_carolinas")).one()
    event = Event(
        replay_key="dec_term_loan",
        event_type="10-Q",
        issuer_id=carolinas.id,
        event_date=date(2026, 3, 13),
        summary="Carolinas term loan",
        status="pending",
    )
    session.add(event)
    session.flush()
    session.add(
        ReplayExpectation(
            key="dec_term_loan",
            issuer_key="duke_carolinas",
            event_type="new_debt_found",
            event_date=date(2026, 3, 13),
            available_from=date(2026, 5, 5),
            title="Duke Carolinas term loan",
            sources=[],
            expected_changes=[{"action": "add_instrument", "key": "dec_tl_2027"}],
            expected_alert=True,
            event_id=event.id,
        )
    )
    # Ensure a schedule exists so compute_metrics has buckets (seed already has one).
    assert session.scalars(
        select(MaturitySchedule).where(MaturitySchedule.issuer_id == carolinas.id)
    ).first()

    run = start_agent_run(session, event)
    assert set_checklist(
        session,
        run,
        "new_loan",
        [
            {
                "key": k,
                "question": k,
                "evidence_needed": k,
            }
            for k in (
                "instrument",
                "amount",
                "rate",
                "maturity",
                "borrower",
                "maturity_effect",
                "counter_evidence",
            )
        ],
    )["ok"]
    for item in session.scalars(select(ChecklistItem).where(ChecklistItem.run_id == run.id)).all():
        item.status = "escalated"
    session.add(
        StateChange(
            event_id=event.id,
            run_id=run.id,
            status="proposed",
            effective_date=date(2026, 3, 13),
            proposed_diff={
                "action": "add_instrument",
                "instrument_key": "dec_tl_2027",
                "name": "Unsecured Term Loan due 2027",
                "coupon": "0",
                "maturity": "2027-03-12",
                "issue_date": "2026-03-13",
                "amount_outstanding": "1000000000",
                "currency": "USD",
                "instrument_type": "loan",
                "issuer_id": carolinas.id,
                "rate_type": "floating",
            },
        )
    )
    session.flush()
    metrics = compute_metrics(session, run)
    assert metrics["ok"], metrics
    out = submit_alert(
        session,
        run,
        summary="Carolinas $1,000,000,000 term loan due 2027 was missing from the bond list.",
        findings=[],
        material=True,
        claims_client=_claims_supported([]),
    )
    assert out["ok"], out
    assert out["material"] is True
    assert out["status"] == "alerted"
    assert any("not in the bond list" in r for r in out["materiality_reasons"])


def test_claims_check_supported_allows_alert(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    out = _happy_path(session, event, filing)
    assert out["alert"]["claims_check"] == "pass"
    assert out["alert"]["status"] == "alerted"
    checks = session.scalars(
        select(AuditEntry).where(
            AuditEntry.run_id == out["run"].id, AuditEntry.action == "check:claims"
        )
    ).all()
    assert checks
    assert checks[-1].detail and checks[-1].detail.get("passed") is True


def test_claims_check_overstated_refuses_with_phrase(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    assert propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )["ok"]
    assert compute_metrics(session, run)["ok"]
    findings = [
        {
            "item_key": key,
            "statement": f"Settled {key} with amount $1,250,000,000 on September 21, 2026.",
            "citations": [fact_ids[key]],
        }
        for key in fact_ids
    ]
    # Mark amount finding overstated; others supported.
    script_findings = []
    for i, f in enumerate(findings):
        if f["item_key"] == "amount":
            script_findings.append(
                {
                    "finding_index": i,
                    "item_key": "amount",
                    "status": "overstated",
                    "unsupported_phrase": "entire capital structure collapses",
                }
            )
        else:
            script_findings.append(
                {
                    "finding_index": i,
                    "item_key": f["item_key"],
                    "status": "supported",
                    "unsupported_phrase": None,
                }
            )
    bad = submit_alert(
        session,
        run,
        summary=(
            "Verizon will redeem all $1,250,000,000 of its 4.329% Notes due 2028 "
            "on September 21, 2026, cutting the FY2028 wall by $1,250,000,000."
        ),
        findings=findings,
        material=True,
        claims_client=FakeClaimsClient(
            [
                {
                    "summary": {"status": "supported", "unsupported_phrase": None},
                    "findings": script_findings,
                }
            ]
        ),
    )
    assert bad["ok"] is False
    assert any("entire capital structure collapses" in p for p in bad["problems"])
    assert any("overstated" in p for p in bad["problems"])
    assert int((run.refusal_counts or {}).get("submit_alert") or 0) >= 1


def test_claims_check_overstated_summary_refuses_even_if_findings_supported(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    assert propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )["ok"]
    assert compute_metrics(session, run)["ok"]
    findings = [
        {
            "item_key": key,
            "statement": f"Settled {key} with amount $1,250,000,000 on September 21, 2026.",
            "citations": [fact_ids[key]],
        }
        for key in fact_ids
    ]
    bad = submit_alert(
        session,
        run,
        summary=(
            "Verizon will redeem all $1,250,000,000 of its 4.329% Notes due 2028 "
            "on September 21, 2026, guaranteeing the issuer avoids bankruptcy forever."
        ),
        findings=findings,
        material=True,
        claims_client=FakeClaimsClient(
            [
                {
                    "summary": {
                        "status": "overstated",
                        "unsupported_phrase": "avoids bankruptcy forever",
                    },
                    "findings": [
                        {
                            "finding_index": i,
                            "item_key": f.get("item_key"),
                            "status": "supported",
                            "unsupported_phrase": None,
                        }
                        for i, f in enumerate(findings)
                    ],
                }
            ]
        ),
    )
    assert bad["ok"] is False
    assert any("avoids bankruptcy forever" in p for p in bad["problems"])
    assert any("summary" in p for p in bad["problems"])
    assert int((run.refusal_counts or {}).get("submit_alert") or 0) >= 1


def test_claims_check_model_failure_fail_closed(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    assert propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )["ok"]
    assert compute_metrics(session, run)["ok"]
    findings = [
        {
            "item_key": key,
            "statement": f"Settled {key} with amount $1,250,000,000 on September 21, 2026.",
            "citations": [fact_ids[key]],
        }
        for key in fact_ids
    ]
    bad = submit_alert(
        session,
        run,
        summary=(
            "Verizon will redeem all $1,250,000,000 of its 4.329% Notes due 2028 "
            "on September 21, 2026, cutting the FY2028 wall by $1,250,000,000."
        ),
        findings=findings,
        material=True,
        claims_client=FakeClaimsClient([LLMError("upstream down", status_code=503)]),
    )
    assert bad["ok"] is False
    assert any("claims check unavailable" in p for p in bad["problems"])


def test_alert_number_check_accepts_scaled_millions(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    """Summary $1bn is supported when quotes have table '1,000' plus '(in millions)'."""
    event, filing, _bond = verizon_event
    amount_quote = (
        "Duke Energy Carolinas reports an unsecured Term loan facility of 1,000 "
        "outstanding under the credit agreement for the quarter"
    )
    scale_quote = (
        "The following table of long-term debt outstanding (in millions) as of "
        "March 31, 2026 should be read together with the accompanying notes"
    )
    filing.body_text = f"{amount_quote}\n{scale_quote}\n{filing.body_text or ''}"
    session.flush()
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    # Escalate other items; only amount-related facts needed for the number check path.
    for item in session.scalars(select(ChecklistItem).where(ChecklistItem.run_id == run.id)).all():
        item.status = "escalated"
    amt = record_fact(session, run, "amount", filing.id, amount_quote, "table shows 1,000")
    scale = record_fact(session, run, "amount", filing.id, scale_quote, "in millions")
    assert amt["ok"] and scale["ok"]
    wall = {
        "buckets": {
            lbl: {"total_outstanding": "0"} for lbl in ("FY2026", "FY2027", "FY2028", "FY2029")
        }
    }
    run.metrics_outputs = [{"id": "scaled", "before": {"issuer": wall}, "after": {"issuer": wall}}]
    session.flush()
    findings = [
        {
            "item_key": "amount",
            "statement": "Outstanding amount is $1,000,000,000 per the 10-Q table.",
            "citations": [amt["fact_id"], scale["fact_id"]],
        }
    ]
    out = submit_alert(
        session,
        run,
        summary="Carolinas term loan of $1,000,000,000 appears in the filing table.",
        findings=findings,
        material=False,
        claims_client=_claims_supported(findings),
    )
    assert out["ok"], out


def test_judgement_upgrade_requires_claims_pass(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    # Use a no-op metrics path: mark redeemed already applied? Instead empty proposed
    # and flat walls via fabricated metrics after a full fact set without changes.
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    wall = {
        "buckets": {
            lbl: {"total_outstanding": "13032000000"}
            for lbl in ("FY2026", "FY2027", "FY2028", "FY2029")
        }
    }
    run.metrics_outputs = [{"id": "flat", "before": {"issuer": wall}, "after": {"issuer": wall}}]
    session.flush()
    findings = [
        {
            "item_key": key,
            "statement": "Noted with $1,250,000,000 context from the notice.",
            "citations": [fid],
        }
        for key, fid in fact_ids.items()
    ]

    refused = submit_alert(
        session,
        run,
        summary="Liquidity judgement on the $1,250,000,000 redemption notice.",
        findings=findings,
        material=True,
        judgement_reason="Make-whole call signals liquidity priority despite flat wall math",
        claims_client=FakeClaimsClient(
            [
                {
                    "summary": {"status": "supported", "unsupported_phrase": None},
                    "findings": [
                        {
                            "finding_index": i,
                            "item_key": f.get("item_key"),
                            "status": "unsupported",
                            "unsupported_phrase": "liquidity priority",
                        }
                        for i, f in enumerate(findings)
                    ],
                }
            ]
        ),
    )
    assert refused["ok"] is False
    assert any("claims check must pass" in p for p in refused["problems"])

    async def _claims_pass(*_a: object, **_k: object) -> ClaimsResult:
        return ClaimsResult(status="pass", problems=[])

    with patch.object(alert_mod, "claims_check", side_effect=_claims_pass):
        ok_alert = submit_alert(
            session,
            run,
            summary="Liquidity judgement on the $1,250,000,000 redemption notice.",
            findings=findings,
            material=True,
            judgement_reason="Make-whole call signals liquidity priority despite flat wall math",
        )
    assert ok_alert["ok"], ok_alert
    assert ok_alert["material"] is True
    assert ok_alert["judgement_applied"] is True
    assert ok_alert["status"] == "alerted"
    assert ok_alert["materiality_reasons"][0].startswith("judgement (agent):")


def test_agent_cannot_downgrade_code_material(
    session: Session, verizon_event: tuple[Event, Filing, Instrument]
) -> None:
    event, filing, _bond = verizon_event
    run = start_agent_run(session, event)
    assert set_checklist(session, run, "redemption", REDEMPTION_ITEMS)["ok"]
    assert read_document(session, run, filing.id, page=0)["ok"]
    fact_ids = _record_all_facts(session, run, filing)
    assert propose_change(
        session,
        run,
        {
            "action": "mark_redeemed",
            "instrument_key": "vz_4329_2028",
            "effective_date": "2026-09-21",
            "evidence": {
                "instrument": fact_ids["instrument"],
                "effective_date": fact_ids["redemption_date"],
            },
        },
    )["ok"]
    assert compute_metrics(session, run)["ok"]
    findings = [
        {
            "item_key": key,
            "statement": f"Settled {key} for $1,250,000,000 on September 21, 2026.",
            "citations": [fact_ids[key]],
        }
        for key in fact_ids
    ]
    forced = submit_alert(
        session,
        run,
        summary=(
            "Verizon redeems $1,250,000,000 of 4.329% Notes due 2028; "
            "FY2028 wall falls by $1,250,000,000."
        ),
        findings=findings,
        material=False,
        claims_client=_claims_supported(findings),
    )
    assert forced["ok"], forced
    assert forced["material"] is True
    assert forced["code_material"] is True
    assert forced["judgement_applied"] is False
    assert forced["status"] == "alerted"
