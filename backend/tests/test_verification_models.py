"""Create-and-query coverage for checklist, fact, check-result and audit models."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    AuditEntry,
    ChecklistItem,
    CheckResult,
    Fact,
    Filing,
    Issuer,
    ResearchRun,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def test_checklist_item(session: Session) -> None:
    run = ResearchRun(question="Verizon redemption")
    session.add(run)
    session.flush()
    item = ChecklistItem(
        run_id=run.id,
        question="What principal amount is being redeemed?",
        evidence_needed="Exact outstanding/redeemed amount from the notice",
        status="open",
    )
    session.add(item)
    session.commit()

    found = session.scalars(select(ChecklistItem).where(ChecklistItem.run_id == run.id)).one()
    assert found.status == "open"
    assert "principal" in found.question.lower()
    assert found.evidence_needed


def test_fact(session: Session) -> None:
    issuer = Issuer(name="Verizon Communications Inc.", cik="732712")
    run = ResearchRun(question="Verizon redemption")
    session.add_all([issuer, run])
    session.flush()
    filing = Filing(
        issuer_id=issuer.id,
        form_type="8-K",
        url="https://www.verizon.com/about/news/example",
        filed_at=date(2026, 8, 20),
        content_hash="abc",
    )
    item = ChecklistItem(
        run_id=run.id,
        question="What principal amount is being redeemed?",
        evidence_needed="Amount from the redemption notice",
        status="open",
    )
    session.add_all([filing, item])
    session.flush()
    fact = Fact(
        run_id=run.id,
        checklist_item_id=item.id,
        filing_id=filing.id,
        location="press release ¶2",
        exact_quote="will redeem all $1.25 billion aggregate principal amount",
        claim="Verizon is redeeming $1.25bn of the 4.329% notes due 2028",
    )
    session.add(fact)
    session.commit()

    found = session.scalars(select(Fact).where(Fact.run_id == run.id)).one()
    assert found.exact_quote.startswith("will redeem")
    assert found.claim.startswith("Verizon is redeeming")
    assert found.filing_id == filing.id
    assert found.checklist_item.question.startswith("What principal")


def test_check_result(session: Session) -> None:
    run = ResearchRun(question="Verizon redemption")
    session.add(run)
    session.flush()
    refused = CheckResult(
        run_id=run.id,
        check_name="quote",
        passed=False,
        problems=["quote not found on that page"],
        attempt=1,
    )
    passed = CheckResult(
        run_id=run.id,
        check_name="quote",
        passed=True,
        problems=[],
        attempt=2,
    )
    session.add_all([refused, passed])
    session.commit()

    results = session.scalars(
        select(CheckResult).where(CheckResult.check_name == "quote").order_by(CheckResult.attempt)
    ).all()
    assert len(results) == 2
    assert results[0].passed is False
    assert results[0].problems == ["quote not found on that page"]
    assert results[1].passed is True
    assert results[1].attempt == 2


def test_audit_entry(session: Session) -> None:
    run = ResearchRun(question="Verizon redemption")
    session.add(run)
    session.flush()
    entries = [
        AuditEntry(
            run_id=run.id,
            actor="agent",
            action="record_fact",
            detail={"checklist_item_id": "x"},
            timestamp=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        ),
        AuditEntry(
            run_id=run.id,
            actor="verification",
            action="quote_check_refused",
            detail={"reason": "quote not found on that page"},
            timestamp=datetime(2026, 8, 20, 12, 0, 1, tzinfo=UTC),
        ),
        AuditEntry(
            run_id=run.id,
            actor="analyst",
            action="confirm_alert",
            detail={"alert_id": "y"},
            timestamp=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
        ),
    ]
    session.add_all(entries)
    session.commit()

    found = session.scalars(
        select(AuditEntry).where(AuditEntry.run_id == run.id).order_by(AuditEntry.timestamp)
    ).all()
    assert [e.actor for e in found] == ["agent", "verification", "analyst"]
    assert found[1].action == "quote_check_refused"
    assert found[1].detail["reason"] == "quote not found on that page"
