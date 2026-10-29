"""Run a single replay event through the real agent loop.

Loads the replay list (if needed), advances to that event's ``available_from`` with
live EDGAR / newsroom clients so source documents are fetched, then runs
``run_event`` with Muse Spark (prompt event_v2).

Usage (from backend/, with .env and seed loaded):

    python -m scripts.run_one_event --replay-key vz_redemption_notice
    python -m scripts.run_one_event --replay-key vz_redemption_notice --dry-run

``--dry-run`` still fetches real documents; only the model is replaced by FakeModel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.fake import FakeModel, function_call, plain_text
from app.agent.loop import RunLimits, run_event
from app.agent.text import html_to_text
from app.config import get_settings
from app.db import SessionLocal
from app.ingest.replay import advance_to, default_clients, load_replay_events
from app.llm import LLMClient
from app.models import Alert, Event, EventFiling, Filing, Issuer, ReplayExpectation
from app.models.trace import AgentRun, AgentStep, ModelCall

MAX_TURNS = 30
ARGS_MAX_CHARS = 180

# Checklist for the Verizon redemption dry-run path.
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


def _configure_logging() -> None:
    """Send library/app logs to stderr so stdout stays a clean run transcript."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _short(value: Any, limit: int = ARGS_MAX_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _ensure_seed(session: Session) -> None:
    n = session.scalar(select(func.count()).select_from(Issuer)) or 0
    if n == 0:
        raise SystemExit(
            "No issuers in the database. Load the year-end seed first:\n"
            "  python -m scripts.load_seed seed/snapshot_2025-12-31.json"
        )


def _ensure_replay(session: Session) -> None:
    n = session.scalar(select(func.count()).select_from(ReplayExpectation)) or 0
    if n == 0:
        _log("Replay expectations empty — loading replay list…")
        load_replay_events(session)
        session.commit()
        n = session.scalar(select(func.count()).select_from(ReplayExpectation)) or 0
        _log(f"  loaded {n} expectation(s)")
    else:
        _log(f"Replay expectations already loaded ({n})")


def _expect(session: Session, replay_key: str) -> ReplayExpectation:
    exp = session.scalars(
        select(ReplayExpectation).where(ReplayExpectation.key == replay_key)
    ).first()
    if exp is None:
        raise SystemExit(f"Unknown replay key {replay_key!r} (not in ReplayExpectation)")
    return exp


def _event_for_key(session: Session, replay_key: str) -> Event:
    event = session.scalars(select(Event).where(Event.replay_key == replay_key)).first()
    if event is None:
        raise SystemExit(f"No Event row for replay_key={replay_key!r} after advance_to")
    return event


def _primary_filing(session: Session, event: Event) -> Filing | None:
    if event.source_filing_id:
        filing = session.get(Filing, event.source_filing_id)
        if filing is not None:
            return filing
    link = session.scalars(
        select(EventFiling).where(EventFiling.event_id == event.id).limit(1)
    ).first()
    if link is None:
        return None
    return session.get(Filing, link.filing_id)


def _sentence_containing(text: str, needle: str) -> str:
    """Return the sentence in ``text`` that contains ``needle`` (live-page resilient)."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    for part in parts:
        candidate = " ".join(part.split())
        if needle in candidate and len(candidate) >= 40:
            return candidate
    idx = text.find(needle)
    if idx < 0:
        raise SystemExit(f"Dry-run: fetched text has no {needle!r}")
    start = text.rfind(".", 0, idx)
    start = 0 if start < 0 else start + 1
    end = text.find(".", idx)
    if end < 0:
        end = min(len(text), idx + 240)
    else:
        end += 1
    candidate = " ".join(text[start:end].split())
    if len(candidate) < 40:
        raise SystemExit(f"Dry-run: sentence for {needle!r} too short: {candidate!r}")
    return candidate


def _vz_quotes_from_live_text(text: str) -> dict[str, str]:
    """Pick verbatim quotes from the live fetched page (not fixture-specific anchors)."""
    coupon = _sentence_containing(text, "4.329%")
    date_sent = _sentence_containing(text, "September 21, 2026")
    treasury = None
    for needle in ("Treasury Rate", "make-whole", "Make-Whole", "redemption price"):
        try:
            treasury = _sentence_containing(text, needle)
            break
        except SystemExit:
            continue
    if treasury is None:
        treasury = coupon
    whole = None
    for needle in ("in whole", "redeem all", "will redeem"):
        try:
            whole = _sentence_containing(text, needle)
            break
        except SystemExit:
            continue
    if whole is None:
        whole = date_sent
    return {
        "instrument": coupon,
        "amount": coupon,
        "redemption_date": date_sent,
        "price_basis": treasury,
        "conditions": whole,
        "maturity_effect": coupon,
        "counter_evidence": whole,
    }


def _dry_run_client(session: Session, event: Event, replay_key: str) -> FakeModel:
    """Scripted FakeModel. Full redemption path for vz_redemption_notice; else smoke."""
    filing = _primary_filing(session, event)
    if replay_key == "vz_redemption_notice":
        if filing is None or not filing.body_text:
            raise SystemExit("Dry-run Verizon path needs a fetched filing with body_text")
        text = filing.body_text
        if "<" in text[:800] and "4.329%" not in text:
            text = html_to_text(text)
        quotes = _vz_quotes_from_live_text(text)
        filing_id = filing.id
        script: list[Any] = [
            function_call("get_issuer_debt", {}, commentary="reading Kernel's debt book"),
            function_call(
                "set_checklist",
                {"kind": "redemption", "items": REDEMPTION_ITEMS},
                commentary="setting redemption checklist",
            ),
            function_call("list_documents", {}, commentary="listing event documents"),
            function_call(
                "read_document",
                {"filing_id": filing_id, "page": 0},
                commentary="reading press release",
            ),
        ]
        for key, quote in quotes.items():
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

        def propose(ctx: Any) -> dict[str, Any]:
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

        def alert(ctx: Any) -> dict[str, Any]:
            facts = ctx.fact_ids()
            findings = [
                {
                    "item_key": key,
                    "statement": (
                        f"Settled {key} with amount $1,250,000,000 on September 21, 2026."
                    ),
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

        script.append(propose)
        script.append(function_call("compute_metrics", {}, commentary="computing metrics"))
        script.append(alert)
        return FakeModel(script)

    _log(
        f"Note: dry-run full path is only scripted for vz_redemption_notice; "
        f"using a short smoke script for {replay_key!r}."
    )
    script = [
        function_call("get_issuer_debt", {}, commentary="debt book"),
        function_call("list_documents", {}, commentary="documents"),
        plain_text("dry-run smoke: no further scripted tools"),
        plain_text("dry-run smoke: still no tool"),
        plain_text("dry-run smoke: pausing"),
    ]
    return FakeModel(script)


def print_turns(session: Session, run: AgentRun) -> None:
    """One stdout line per turn: n | commentary | tool | args | result."""
    steps = session.scalars(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
    ).all()
    if not steps:
        print("(no agent steps recorded)")
        return
    for step in steps:
        out = step.output or {}
        commentary = _short((out.get("commentary") or "").replace("\n", " "), 80)
        tool = out.get("tool") or "-"
        args = _short(out.get("arguments") or {}, 100)
        result = out.get("result")
        if isinstance(result, dict):
            if result.get("ok"):
                outcome = "ok"
            else:
                outcome = f"refused:{_short(result.get('reason') or result.get('problems'), 80)}"
        elif out.get("nudge"):
            outcome = f"nudge:{out.get('nudge')}"
        else:
            outcome = step.status
        extras = out.get("extra_calls_refused") or 0
        extra_s = f" extras={extras}" if extras else ""
        print(f"{step.sequence}\t{commentary}\t{tool}\t{args}\t{outcome}{extra_s}")


def _issuer_wall(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept either a wall dict or ``{issuer, group}`` from compute_metrics."""
    if not metrics:
        return None
    if "buckets" in metrics:
        return metrics
    nested = metrics.get("issuer")
    if isinstance(nested, dict) and "buckets" in nested:
        return nested
    return None


def _bucket_totals(wall: dict[str, Any] | None) -> dict[str, Decimal]:
    buckets = (wall or {}).get("buckets") or {}
    out: dict[str, Decimal] = {}
    for label, bucket in buckets.items():
        raw = (bucket or {}).get("total_outstanding")
        if raw is None:
            continue
        out[str(label)] = Decimal(str(raw))
    return out


def _affected_bucket(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, Decimal, Decimal] | None:
    b = _bucket_totals(_issuer_wall(before))
    a = _bucket_totals(_issuer_wall(after))
    labels = set(b) | set(a)
    if not labels:
        return None
    best_label = sorted(labels)[0]
    best_delta = Decimal("-1")
    before_v = Decimal("0")
    after_v = Decimal("0")
    for label in labels:
        bv = b.get(label, Decimal("0"))
        av = a.get(label, Decimal("0"))
        delta = abs(bv - av)
        if delta > best_delta:
            best_delta = delta
            best_label = label
            before_v, after_v = bv, av
    return best_label, before_v, after_v


def print_summary(session: Session, run: AgentRun, *, wall_started: float) -> None:
    print("---")
    print(f"status\t{run.status}")
    print(f"run_id\t{run.id}")
    if run.error:
        print(f"error\t{run.error}")

    alert = session.scalars(select(Alert).where(Alert.run_id == run.id)).first()
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    if alert is not None:
        print(f"alert\t{alert.title}")
        print(f"summary\t{alert.summary}")
        print(f"materiality\t{alert.materiality_reasons}")
        before = alert.before_metrics
        after = alert.after_metrics
    else:
        meta = run.result or {}
        if meta.get("summary"):
            print(f"logged_summary\t{meta.get('summary')}")
            print(f"materiality\t{meta.get('materiality')}")
        outputs = list(run.metrics_outputs or [])
        if outputs:
            latest = outputs[-1]
            before = latest.get("before")
            after = latest.get("after")

    affected = _affected_bucket(before, after)
    if affected:
        label, bv, av = affected
        print(f"wall\t{label}\tbefore={bv}\tafter={av}\tdelta={av - bv}")
    else:
        print("wall\t(none)")

    calls = session.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all()
    in_tok = sum(int(c.input_tokens or 0) for c in calls)
    out_tok = sum(int(c.output_tokens or 0) for c in calls)
    cost = run.total_cost_usd or sum((c.cost_usd or Decimal("0")) for c in calls)
    duration_s = time.perf_counter() - wall_started
    meta = run.result or {}
    if meta.get("started_at") and run.completed_at:
        try:
            started = datetime.fromisoformat(str(meta["started_at"]).replace("Z", "+00:00"))
            duration_s = (run.completed_at - started).total_seconds()
        except ValueError:
            pass
    print(f"tokens\tinput={in_tok}\toutput={out_tok}\ttotal={in_tok + out_tok}")
    print(f"cost_usd\t{cost}")
    print(f"duration_s\t{duration_s:.1f}")
    print(f"prompt_version\t{meta.get('prompt_version')}")
    print(f"model_calls\t{len(calls)}")


async def _run(
    *,
    replay_key: str,
    dry_run: bool,
) -> int:
    settings = get_settings()
    clients = default_clients()
    wall_started = time.perf_counter()
    run: AgentRun | None = None

    try:
        with SessionLocal() as session:
            _ensure_seed(session)
            _ensure_replay(session)
            exp = _expect(session, replay_key)
            _log(
                f"Advancing replay to {exp.available_from.isoformat()} "
                f"for {replay_key!r} (live EDGAR/newsroom)…"
            )
            try:
                created = advance_to(session, exp.available_from, clients)
                session.commit()
            except Exception as exc:
                session.rollback()
                _log(f"FAILED while fetching sources / advance_to: {exc}")
                traceback.print_exc(file=sys.stderr)
                return 1
            _log(f"  advance_to created {len(created)} new event(s)")

            event = _event_for_key(session, replay_key)
            filing = _primary_filing(session, event)
            _log(
                f"Event {event.id} status={event.status} "
                f"date={event.event_date} filing={filing.id if filing else None}"
            )

            if dry_run:
                model_client: Any = _dry_run_client(session, event, replay_key)
                model_label = "FakeModel"
            else:
                if not settings.model_api_key or not settings.meta_model_api_base_url:
                    _log("FAILED: MODEL_API_KEY / META_MODEL_API_BASE_URL not set")
                    return 1
                # Loop persists ModelCall after committing AgentStep; log-only recorder here.
                model_client = LLMClient(settings=settings)
                model_label = settings.default_model or "muse-spark-1.3"

            limits = RunLimits(
                max_turns=MAX_TURNS,
                max_cost_usd=float(settings.max_run_cost_usd),
                max_tokens=int(settings.max_run_tokens),
            )
            _log(
                f"Starting run_event with {model_label}, "
                f"max_turns={limits.max_turns}, max_cost_usd={limits.max_cost_usd}, "
                f"max_tokens={limits.max_tokens}"
            )

            try:
                run = await run_event(
                    session,
                    event.id,
                    model_client,
                    limits,
                    model=(None if dry_run else (settings.default_model or "muse-spark-1.3")),
                    print_turns=False,
                )
            except Exception as exc:
                session.commit()
                if run is None and event.run_id:
                    run = session.get(AgentRun, event.run_id)
                _log(f"FAILED during run_event: {type(exc).__name__}: {exc}")
                traceback.print_exc(file=sys.stderr)
                if run is not None:
                    session.refresh(run)
                    print_turns(session, run)
                    print_summary(session, run, wall_started=wall_started)
                return 1
            finally:
                close = getattr(model_client, "aclose", None)
                if callable(close):
                    await close()

            session.refresh(run)
            print_turns(session, run)
            print_summary(session, run, wall_started=wall_started)

            if run.status not in {"alerted", "logged", "needs_input"}:
                _log(f"Run did not finish cleanly (status={run.status}).")
                return 1
            return 0
    finally:
        for client in (clients.edgar, clients.newsroom):
            close = getattr(client, "close", None)
            if callable(close):
                close()


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Fetch one replay event's sources and run the agent loop on it"
    )
    parser.add_argument(
        "--replay-key",
        required=True,
        help="ReplayExpectation.key (e.g. vz_redemption_notice)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use FakeModel instead of the Meta API (still fetches real documents)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(replay_key=args.replay_key, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
