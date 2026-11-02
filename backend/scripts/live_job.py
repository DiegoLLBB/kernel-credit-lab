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
    ensure_live_config,
    pending_live_events,
    poll_all,
)
from app.insight.generate import generate_impact, generate_snapshot
from app.llm import LLMClient
from app.models import Alert, Event, Issuer
from app.replay.runner import process_one_event
from app.sources.edgar import EdgarClient
from app.sources.newsroom import NewsroomClient


def _outcome_label(status: str, material: bool | None) -> str:
    if status == "alerted" or material is True:
        return "alerted"
    if status == "logged" or material is False:
        return "logged"
    if status in {"needs_input", "refused"}:
        return "refused"
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


async def _run(*, since: date, until: date, max_cost_usd: float) -> int:
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

        docs = poll_all(session, edgar, newsroom, since=since, until=until)
        session.commit()
        new_docs = [d for d in docs if d.created]
        print(
            f"live: polled {len(docs)} documents since {since.isoformat()} "
            f"({len(new_docs)} new events)"
        )
        for d in new_docs:
            print(f"live: new {d.issuer_key}\t{d.form}\t{d.filed_at.isoformat()}\t{d.identity}")

        pending = pending_live_events(session)
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
                outcome = await process_one_event(session, event, llm, limits=limits)
                session.commit()
                spent += float(outcome.cost_usd or 0)
                label = _outcome_label(outcome.status, outcome.material)
                print(
                    f"live: {issuer.key if issuer else '?'}\t{form}\t{label}\t"
                    f"{outcome.cost_usd:.4f}"
                )
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
    args = parser.parse_args(argv)

    # Default backfills from the post-replay cutoff so the first run picks up
    # any Q3 10-Q (etc.) filed since 27 Oct 2026; later runs dedupe by accession/URL.
    since = args.since or DEFAULT_LIVE_SINCE

    return asyncio.run(
        _run(since=since, until=args.until or date.today(), max_cost_usd=args.max_cost_usd)
    )


if __name__ == "__main__":
    raise SystemExit(main())
