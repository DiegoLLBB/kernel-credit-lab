"""Cloud job: poll EDGAR + newsrooms, run the frozen agent on new live events.

Usage (from backend/):
    python -m scripts.live_job
    python -m scripts.live_job --since 2026-10-27 --max-cost-usd 5

First execution writes the ``live_config`` freeze row (go-live time, git SHA,
prompt version). Subsequent runs refuse to proceed if PROMPT_VERSION drifts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from datetime import date

from sqlalchemy import select

from app.agent.loop import RunLimits
from app.agent.prompts import PROMPT_VERSION
from app.config import get_settings
from app.db import SessionLocal
from app.ingest.live import (
    DEFAULT_LIVE_SINCE,
    MAX_LIVE_RESUMES,
    ensure_live_config,
    live_resume_attempts,
    mark_live_resume_attempt,
    pending_live_events,
    poll_all,
)
from app.insight.brief import generate_daily_brief
from app.insight.generate import generate_impact, generate_snapshot
from app.llm import LLMClient
from app.models import Alert, Event, Issuer
from app.models.trace import AgentRun
from app.replay.runner import process_one_event
from app.sources.edgar import EdgarClient
from app.sources.fred import FredError
from app.sources.newsroom import NewsroomClient
from scripts.load_rates import DEFAULT_START, load_rates


def _outcome_label(status: str, material: bool | None) -> str:
    if status == "alerted" or material is True:
        return "alerted"
    if status == "logged" or material is False:
        return "logged"
    if status in {"needs_input", "refused"}:
        return "refused"
    if status == "paused":
        return "paused"
    if status == "failed":
        return "failed"
    return status or "failed"


async def _insights_for_event(session, event: Event) -> None:
    issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
    if issuer is not None and issuer.key:
        try:
            await generate_snapshot(session, issuer.key, date.today())
            print(f"live: snapshot {issuer.key} refreshed")
        except Exception as exc:
            print(f"live: snapshot {issuer.key} failed: {exc}", file=sys.stderr)
    for alert in session.scalars(select(Alert).where(Alert.event_id == event.id)).all():
        try:
            row = await generate_impact(session, alert.id)
            print(f"live: impact {alert.id[:8]}: {row.status}")
        except Exception as exc:
            print(f"live: impact {alert.id[:8]} failed: {exc}", file=sys.stderr)


async def _run(
    *,
    since: date,
    until: date,
    max_cost_usd: float,
    resume_only: bool = False,
    issuer_keys: list[str] | None = None,
) -> int:
    settings = get_settings()
    edgar = EdgarClient(settings=settings)
    newsroom = NewsroomClient(settings=settings)
    llm = LLMClient(settings)
    processed = 0
    errors = 0

    with SessionLocal() as session:
        cfg = ensure_live_config(session, prompt_version=PROMPT_VERSION)
        session.commit()
        if cfg.prompt_version != PROMPT_VERSION:
            print(
                f"live: REFUSED — frozen prompt {cfg.prompt_version!r} != "
                f"running {PROMPT_VERSION!r}. Agent must stay frozen after go-live.",
                file=sys.stderr,
            )
            return 2
        print(
            f"live: go_live_at={cfg.go_live_at.isoformat()} "
            f"git_sha={cfg.git_sha} prompt={cfg.prompt_version}"
        )

        # FRED Treasury + FX refresh (same schedule as this job: twice daily).
        # Does not touch the agent; failures leave existing RateObservation rows.
        try:
            rate_counts = load_rates(start=DEFAULT_START, end=date.today(), session=session)
            session.commit()
            rate_total = sum(rate_counts.values())
            print(
                f"live: load_rates {DEFAULT_START.isoformat()} → {date.today().isoformat()} "
                f"({rate_total} observations upserted)"
            )
            for series_id, n in rate_counts.items():
                print(f"live: load_rates {series_id}: {n}")
        except FredError as exc:
            session.rollback()
            print(f"live: load_rates FAILED (continuing): {exc}", file=sys.stderr)

        if not resume_only:
            docs = poll_all(session, edgar, newsroom, since=since, until=until)
            session.commit()
            new_docs = [d for d in docs if d.created]
            print(
                f"live: polled {len(docs)} documents since {since.isoformat()} "
                f"({len(new_docs)} new events)"
            )
            for d in new_docs:
                print(f"live: new {d.issuer_key}\t{d.form}\t{d.filed_at.isoformat()}\t{d.identity}")
        else:
            print("live: resume-only — skipping EDGAR/newsroom poll")

        pending = pending_live_events(session)
        if issuer_keys:
            want = {k.lower() for k in issuer_keys}
            filtered = []
            for event in pending:
                issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
                if issuer and (issuer.key or "").lower() in want:
                    filtered.append(event)
            pending = filtered
            print(f"live: filtered to issuers {sorted(want)} → {len(pending)} events")

        # Log paused live runs left alone after their single resume attempt.
        for event in session.scalars(
            select(Event).where(Event.source == "live").order_by(Event.event_date.asc())
        ).all():
            if not event.run_id:
                continue
            run = session.get(AgentRun, event.run_id)
            if run is None or run.status != "paused":
                continue
            if live_resume_attempts(run) < MAX_LIVE_RESUMES:
                continue
            issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
            print(
                f"live: skip {issuer.key if issuer else '?'}\t{event.event_type}\t"
                f"paused (resume already used)"
            )

        spent = 0.0
        for event in pending:
            if spent >= max_cost_usd:
                print(f"live: cost cap ${max_cost_usd:.2f} reached; stopping")
                break
            remaining = max(0.0, max_cost_usd - spent)
            limits = RunLimits(max_cost_usd=min(remaining, settings.max_run_cost_usd or remaining))
            issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
            form = event.event_type
            try:
                run_before = session.get(AgentRun, event.run_id) if event.run_id else None
                prior_cost = float(run_before.total_cost_usd or 0) if run_before else 0.0
                if run_before is not None and run_before.status == "paused":
                    n = mark_live_resume_attempt(run_before)
                    session.commit()
                    print(
                        f"live: resume {issuer.key if issuer else '?'}\t{form}\t"
                        f"attempt {n}/{MAX_LIVE_RESUMES}"
                    )
                outcome = await process_one_event(session, event, llm, limits=limits)
                session.commit()
                # Charge only new spend (run.total_cost_usd is cumulative across resumes).
                new_total = float(outcome.cost_usd or 0)
                incremental = max(0.0, new_total - prior_cost)
                spent += incremental
                label = _outcome_label(outcome.status, outcome.material)
                reason = (outcome.detail or "").replace("\t", " ").strip()
                print(
                    f"live: {issuer.key if issuer else '?'}\t{form}\t{label}\t"
                    f"{incremental:.4f}" + (f"\t{reason}" if reason else "")
                )
                # Don't re-pay for snapshot prose on paused / needs-an-analyst exits.
                if outcome.status in {"alerted", "logged"}:
                    await _insights_for_event(session, event)
                    session.commit()
                processed += 1
            except Exception as exc:
                errors += 1
                session.rollback()
                traceback.print_exc()
                print(
                    f"live: {issuer.key if issuer else '?'}\t{form}\tfailed\t0.0000\t{exc}",
                    file=sys.stderr,
                )
                event = session.get(Event, event.id)
                if event is not None:
                    event.status = "failed"
                    session.commit()

        # Muse Wonder daily brief (display-only; after insights). Cap $0.50 inside.
        try:
            brief = await generate_daily_brief(session, date.today())
            print(
                f"live: brief @ {brief.as_of}: {brief.status} "
                f"kept={len(brief.sentences or [])} refused={len(brief.dropped or [])} "
                f"cost=${float(brief.cost_usd or 0):.4f}"
            )
        except Exception as exc:
            errors += 1
            traceback.print_exc()
            print(f"live: brief FAILED: {exc}", file=sys.stderr)

    print(f"live: complete processed={processed} errors={errors}")
    return 1 if errors and not processed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live EDGAR/newsroom poll + agent.")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        help=f"Inclusive start date (default: go-live date or {DEFAULT_LIVE_SINCE})",
    )
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    parser.add_argument("--max-cost-usd", type=float, default=5.0)
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="Skip polling; only resume paused/pending/failed live events.",
    )
    parser.add_argument(
        "--issuer",
        action="append",
        default=None,
        dest="issuers",
        help="Limit to issuer key(s), e.g. --issuer boeing --issuer intel",
    )
    args = parser.parse_args(argv)

    # Default backfills from the post-replay cutoff so the first run picks up
    # any Q3 10-Q (etc.) filed since 27 Oct 2026; later runs dedupe by accession/URL.
    since = args.since or DEFAULT_LIVE_SINCE

    return asyncio.run(
        _run(
            since=since,
            until=args.until or date.today(),
            max_cost_usd=args.max_cost_usd,
            resume_only=args.resume_only,
            issuer_keys=args.issuers,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
