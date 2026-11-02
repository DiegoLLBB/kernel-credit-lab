"""Plain one-shot Spark baseline over replay source documents (no tools).

For each of the 29 replay expectations / events: one Muse Spark call with the
event's source document truncated to the agent's first ``read_document`` page
(``PAGE_CHARS`` = 8000) and a fixed maturity-change question. Score with the
same alert confusion matrix as the Kernel agent, plus an exact-substring check
for amounts and quotes that are not in the document (= unsupported claims).

Usage (from backend/):
    python -m scripts.run_baseline [--max-cost-usd 8]

Stores ``replay_evaluation`` with ``kind="baseline"``. Cap $8.
"""

from __future__ import annotations

import argparse
import asyncio
import traceback
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.text import PAGE_CHARS
from app.config import get_settings
from app.db import SessionLocal
from app.eval.replay_eval import AlertConfusion, _decimal_or_none, _git_sha
from app.eval.summary import build_eval_summary
from app.llm.client import LLMClient, LLMError, Provider, StructuredOutputError
from app.llm.recorder import db_recorder
from app.models import (
    Alert,
    Event,
    EventFiling,
    Filing,
    Issuer,
    ReplayEvaluation,
    ReplayExpectation,
)

PROMPT_VERSION = "baseline_v1"
BASELINE_KIND = "baseline"
DEFAULT_COST_CAP = 8.0


class BaselineAnswer(BaseModel):
    material: bool = Field(description="True if debt due 2026-2029 changed materially")
    fiscal_year: int | None = Field(default=None, description="Affected fiscal year if any")
    amount_change_usd: float | None = Field(
        default=None, description="Signed USD change if stated in the document"
    )
    quote: str | None = Field(
        default=None, description="Exact substring from the document supporting the answer"
    )


def _document_text(session: Session, event: Event) -> str:
    filing_ids: list[str] = []
    if event.source_filing_id:
        filing_ids.append(event.source_filing_id)
    for link in session.scalars(select(EventFiling).where(EventFiling.event_id == event.id)).all():
        if link.filing_id not in filing_ids:
            filing_ids.append(link.filing_id)
    chunks: list[str] = []
    for fid in filing_ids:
        filing = session.get(Filing, fid)
        if filing is None or not filing.body_text:
            continue
        chunks.append(filing.body_text)
    text = "\n\n".join(chunks).strip()
    if not text:
        return ""
    return text[:PAGE_CHARS]


def _normalize_for_search(value: str) -> str:
    return " ".join(value.split()).lower()


def _unsupported(answer: BaselineAnswer, document: str) -> list[str]:
    """Amounts or quotes not found as exact substrings in the document."""
    problems: list[str] = []
    hay = document
    hay_norm = _normalize_for_search(document)
    if answer.quote:
        q = answer.quote.strip()
        if q and q not in hay and _normalize_for_search(q) not in hay_norm:
            problems.append(f"quote not in document: {q[:120]}")
    if answer.amount_change_usd is not None:
        amount = answer.amount_change_usd
        candidates = {
            str(int(amount)) if float(amount).is_integer() else str(amount),
            f"{amount:,.0f}",
            f"{amount:,.2f}",
            f"{abs(amount):,.0f}",
            f"{abs(int(amount))}" if float(amount).is_integer() else f"{abs(amount)}",
        }
        # Also accept millions / billions shorthand present in prose.
        millions = abs(amount) / 1_000_000
        if millions >= 1:
            candidates.add(f"{millions:.0f}")
            candidates.add(f"{millions:.1f}")
            candidates.add(f"{millions:,.0f}")
        if not any(c and c in hay for c in candidates):
            problems.append(f"amount not in document: {amount}")
    return problems


def _question(issuer_name: str, available_from: date) -> str:
    return (
        f"As of {available_from.isoformat()}, did {issuer_name}'s debt due in 2026–2029 "
        "change materially (≥ $500M or 10% of a year's maturities)? Answer as JSON: "
        "material (bool), fiscal_year, amount_change_usd, quote."
    )


async def _ask_one(
    client: LLMClient,
    *,
    issuer_name: str,
    available_from: date,
    document: str,
) -> tuple[BaselineAnswer | None, str | None, Decimal]:
    settings = get_settings()
    model = settings.default_model or "muse-spark-1.3"
    system = (
        "You answer from the provided document only. No tools. If the document does "
        "not support a number or quote, leave amount_change_usd and quote null. "
        "Never invent figures."
    )
    user = (
        _question(issuer_name, available_from)
        + "\n\n--- DOCUMENT (truncated to first agent page) ---\n\n"
        + (document or "(empty document)")
    )
    try:
        parsed, llm = await client.structured_json(
            provider=Provider.META,
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            schema=BaselineAnswer,
            task="baseline_rag",
            prompt_version=PROMPT_VERSION,
            temperature=0.0,
            run_id=None,
        )
        cost = Decimal(str(llm.cost_usd or 0)) if llm and llm.cost_usd is not None else Decimal("0")
        if isinstance(parsed, BaselineAnswer):
            return parsed, None, cost
        return None, "unexpected response type", cost
    except (LLMError, StructuredOutputError, OSError, TimeoutError, RuntimeError) as exc:
        return None, str(exc)[:300], Decimal("0")


def _score_baseline(
    session: Session,
    answers: dict[str, dict[str, Any]],
    *,
    cost_usd: Decimal,
) -> ReplayEvaluation:
    """Build confusion counts with the same merge rules as the agent evaluator."""
    expectations = session.scalars(select(ReplayExpectation).order_by(ReplayExpectation.key)).all()
    siblings: dict[str, list[ReplayExpectation]] = {}
    for exp in expectations:
        if exp.event_id:
            siblings.setdefault(exp.event_id, []).append(exp)

    alert = AlertConfusion()
    per_event_dicts: list[dict[str, Any]] = []
    unsupported_total = 0
    scored_event_ids: set[str] = set()

    for exp in expectations:
        own_expected = bool(exp.expected_alert)
        if exp.event_id and exp.event_id in siblings:
            group_expected = any(bool(s.expected_alert) for s in siblings[exp.event_id])
            score_once = exp.event_id not in scored_event_ids
            if score_once:
                scored_event_ids.add(exp.event_id)
        else:
            group_expected = own_expected
            score_once = True

        payload = answers.get(exp.key) or {}
        material = payload.get("material")
        alerted = bool(material) if material is not None else None
        problems = list(payload.get("unsupported") or [])
        unsupported_total += len(problems)
        if material is None:
            got = "failed"
        elif alerted:
            got = "alerted"
        else:
            got = "logged"

        miss = False
        if score_once:
            if alerted is None:
                if group_expected:
                    alert.false_negative += 1
                    alert.failed_expected_alert += 1
                else:
                    alert.failed_expected_no_alert += 1
                miss = bool(own_expected)
            elif group_expected and alerted:
                alert.true_positive += 1
            elif group_expected and not alerted:
                alert.false_negative += 1
                miss = True
            elif not group_expected and alerted:
                alert.false_positive += 1
                miss = True
            else:
                alert.true_negative += 1
            if (own_expected and alerted is not True) or (
                not own_expected and alerted is True and not group_expected
            ):
                miss = True
        else:
            if (own_expected and alerted is not True) or (
                not own_expected and alerted is True and not group_expected
            ):
                miss = True

        per_event_dicts.append(
            {
                "replay_key": exp.key,
                "event_date": str(exp.event_date or ""),
                "status": got,
                "cost_usd": float(payload.get("cost_usd") or 0),
                "tokens": 0,
                "turns": 1,
                "coverage_score": None,
                "expected_alert": own_expected,
                "got": got,
                "miss": miss,
                "unsupported": problems,
                "material": material,
                "amount_change_usd": payload.get("amount_change_usd"),
                "quote": payload.get("quote"),
            }
        )

    row = ReplayEvaluation(
        kind=BASELINE_KIND,
        precision=_decimal_or_none(alert.precision),
        recall=_decimal_or_none(alert.recall),
        incomplete=False,
        cost_usd=_decimal_or_none(cost_usd),
        event_count=len({e.event_id for e in expectations if e.event_id}) or len(expectations),
        expectation_count=len(expectations),
        per_event=per_event_dicts,
        alert_counts=alert.to_dict(),
        unsupported_claims=unsupported_total,
        git_sha=_git_sha(),
        prompt_version=PROMPT_VERSION,
        error=None,
    )
    session.add(row)
    session.flush()
    return row


def count_agent_unsupported(session: Session) -> int:
    """Verify Kernel alerts: evidence quotes must appear in source filings."""
    bad = 0
    for alert in session.scalars(select(Alert)).all():
        event = session.get(Event, alert.event_id) if alert.event_id else None
        if event is None or event.source == "live":
            continue
        full_parts: list[str] = []
        if event.source_filing_id:
            filing = session.get(Filing, event.source_filing_id)
            if filing and filing.body_text:
                full_parts.append(filing.body_text)
        for link in session.scalars(
            select(EventFiling).where(EventFiling.event_id == event.id)
        ).all():
            filing = session.get(Filing, link.filing_id)
            if filing and filing.body_text:
                full_parts.append(filing.body_text)
        full = "\n".join(full_parts)
        if not full:
            continue
        full_norm = _normalize_for_search(full)
        for finding in alert.findings or []:
            if not isinstance(finding, dict):
                continue
            for cite in finding.get("citations") or []:
                if not isinstance(cite, dict):
                    continue
                quote = (cite.get("exact_quote") or "").strip()
                if not quote:
                    continue
                if quote not in full and _normalize_for_search(quote) not in full_norm:
                    bad += 1
    return bad


async def _run(*, max_cost_usd: float) -> int:
    answers: dict[str, dict[str, Any]] = {}
    spent = Decimal("0")
    client = LLMClient(recorder=db_recorder)
    try:
        with SessionLocal() as session:
            expectations = session.scalars(
                select(ReplayExpectation).order_by(ReplayExpectation.key)
            ).all()
            print(f"baseline: {len(expectations)} expectations, cap ${max_cost_usd:.2f}")
            for exp in expectations:
                if float(spent) >= max_cost_usd:
                    print("baseline: cost cap reached; remaining events scored as failed")
                    answers[exp.key] = {
                        "material": None,
                        "unsupported": ["skipped: cost cap"],
                        "cost_usd": 0,
                    }
                    continue
                event = session.get(Event, exp.event_id) if exp.event_id else None
                if event is None and exp.sources:
                    # Fall back: match replay_key on Event.
                    event = session.scalar(select(Event).where(Event.replay_key == exp.key))
                issuer = None
                if event and event.issuer_id:
                    issuer = session.get(Issuer, event.issuer_id)
                if issuer is None and exp.issuer_key:
                    issuer = session.scalar(select(Issuer).where(Issuer.key == exp.issuer_key))
                issuer_name = (issuer.name if issuer else None) or exp.issuer_key or "the issuer"
                available = exp.available_from or exp.event_date or date.today()
                document = _document_text(session, event) if event else ""
                try:
                    answer, err, cost = await _ask_one(
                        client,
                        issuer_name=issuer_name,
                        available_from=available,
                        document=document,
                    )
                except Exception as exc:
                    traceback.print_exc()
                    answer, err, cost = None, str(exc)[:300], Decimal("0")
                spent += cost
                if answer is None:
                    print(f"baseline: {exp.key}\tfailed\t{err}")
                    answers[exp.key] = {
                        "material": None,
                        "unsupported": [err or "generation failed"],
                        "cost_usd": float(cost),
                    }
                    continue
                problems = _unsupported(answer, document)
                print(
                    f"baseline: {exp.key}\t"
                    f"{'alerted' if answer.material else 'logged'}\t"
                    f"${cost:.4f}\tunsupported={len(problems)}"
                )
                answers[exp.key] = {
                    "material": answer.material,
                    "fiscal_year": answer.fiscal_year,
                    "amount_change_usd": answer.amount_change_usd,
                    "quote": answer.quote,
                    "unsupported": problems,
                    "cost_usd": float(cost),
                }

            row = _score_baseline(session, answers, cost_usd=spent)
            agent_unsupported = count_agent_unsupported(session)
            # Patch latest agent eval row's unsupported_claims if present.
            agent_row = session.scalars(
                select(ReplayEvaluation)
                .where(ReplayEvaluation.kind == "agent")
                .order_by(ReplayEvaluation.evaluated_at.desc())
            ).first()
            if agent_row is not None:
                agent_row.unsupported_claims = agent_unsupported
            session.commit()

            summary = build_eval_summary(session, row, kind=BASELINE_KIND)
            print(
                f"baseline: {summary['text'] if summary else 'no summary'} · "
                f"unsupported claims {row.unsupported_claims} · "
                f"cost ${float(spent):.4f}"
            )
            print(f"baseline: Kernel agent unsupported claims (verify) = {agent_unsupported}")
            if summary and summary.get("caught") is not None:
                agent_sum = build_eval_summary(session, kind="agent")
                if agent_sum:
                    print(f"baseline: Kernel agent: {agent_sum['text']}")
                    print(f"baseline: one-shot model: {summary['text']}")
            return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_COST_CAP)
    args = parser.parse_args(argv)
    return asyncio.run(_run(max_cost_usd=args.max_cost_usd))


if __name__ == "__main__":
    raise SystemExit(main())
