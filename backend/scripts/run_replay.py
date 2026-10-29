"""Run the full 2026 replay day by day and write an evaluation report.

Usage (from backend/, with .env and seed loaded):

    python -m scripts.run_replay
    python -m scripts.run_replay --from 2026-01-01 --until 2026-10-27
    python -m scripts.run_replay --only vz_redemption_notice,vz_tender_results
    python -m scripts.run_replay --max-cost-usd 40
    python -m scripts.run_replay --eval-only replay_runs/20260101T120000Z.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.eval.replay_eval import evaluate_replay, write_eval_reports
from app.ingest.replay import DEFAULT_REPLAY_PATH, default_clients
from app.llm import LLMClient
from app.models import Issuer, ReplayExpectation
from app.replay.runner import DEFAULT_RUNS_DIR, ReplayResult, run_replay
from scripts.load_seed import load_seed

SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "snapshot_2025-12-31.json"


def _configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _ensure_seed(session: Session) -> None:
    n = session.scalar(select(func.count()).select_from(Issuer)) or 0
    if n == 0:
        _log(f"Loading year-end seed from {SEED_PATH}…")
        load_seed(session, SEED_PATH)
        session.commit()


def _parse_date(value: str | None, *, flag: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{flag}: invalid date {value!r}") from exc


def _write_eval(session: Session, result: ReplayResult, *, replay_path: Path | None) -> None:
    log = json.loads(result.log_path.read_text(encoding="utf-8"))
    only = {str(k) for k in (log.get("only_keys") or [])} or None
    report = evaluate_replay(
        session,
        replay_path=replay_path or DEFAULT_REPLAY_PATH,
        log=log,
        only_keys=only,
    )
    md_path, json_path = write_eval_reports(report, result.log_path)
    print(f"eval_md\t{md_path}")
    print(f"eval_json\t{json_path}")
    prec = report.alert.precision
    rec = report.alert.recall
    print(f"precision\t{prec if prec is not None else 'n/a'}")
    print(f"recall\t{rec if rec is not None else 'n/a'}")
    if report.incomplete:
        print(f"incomplete\t{report.incomplete}")
    failed_n = report.alert.failed_total
    if failed_n:
        print(f"failed\t{failed_n}")
    if report.error:
        print(f"error\t{report.error}")


async def _async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Kernel replay day by day.")
    parser.add_argument("--from", dest="window_from", default=None, help="Inclusive start date")
    parser.add_argument("--until", dest="window_until", default=None, help="Inclusive end date")
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated ReplayExpectation keys to run (others left pending)",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="Stop when total event-run cost reaches this USD amount",
    )
    parser.add_argument(
        "--replay-path",
        type=Path,
        default=None,
        help=f"Replay JSON (default: {DEFAULT_REPLAY_PATH})",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help=f"Directory for JSON logs (default: {DEFAULT_RUNS_DIR})",
    )
    parser.add_argument(
        "--eval-only",
        type=Path,
        default=None,
        help="Skip the runner; evaluate an existing replay_runs/*.json log",
    )
    parser.add_argument(
        "--no-eval",
        action="store_true",
        help="Write the run log but skip the evaluation report",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    settings = get_settings()

    if args.eval_only is not None:
        log_path = Path(args.eval_only)
        log = json.loads(log_path.read_text(encoding="utf-8"))
        only = {str(k) for k in (log.get("only_keys") or [])} or None
        with SessionLocal() as session:
            report = evaluate_replay(
                session,
                replay_path=args.replay_path or DEFAULT_REPLAY_PATH,
                log=log,
                only_keys=only,
            )
            md_path, json_path = write_eval_reports(report, log_path)
        print(f"eval_md\t{md_path}")
        print(f"eval_json\t{json_path}")
        prec = report.alert.precision
        rec = report.alert.recall
        print(f"precision\t{prec if prec is not None else 'n/a'}")
        print(f"recall\t{rec if rec is not None else 'n/a'}")
        return 0

    only_keys = None
    if args.only:
        only_keys = {k.strip() for k in args.only.split(",") if k.strip()}

    clients = default_clients()
    llm = LLMClient(settings)
    result: ReplayResult | None = None
    try:
        with SessionLocal() as session:
            _ensure_seed(session)
            n_exp = session.scalar(select(func.count()).select_from(ReplayExpectation)) or 0
            if n_exp == 0:
                _log("Loading replay expectations…")

            def client_factory(_event):
                return llm

            try:
                result = await run_replay(
                    session,
                    clients,
                    client_factory,
                    window_from=_parse_date(args.window_from, flag="--from"),
                    window_until=_parse_date(args.window_until, flag="--until"),
                    only_keys=only_keys,
                    max_cost_usd=args.max_cost_usd,
                    replay_path=args.replay_path,
                    runs_dir=args.runs_dir,
                    model=settings.default_model or "muse-spark-1.3",
                    print_line=print,
                )
            finally:
                # Always emit summary + eval when we have a log (incl. cost/error stop).
                if result is not None:
                    print(f"log\t{result.log_path}")
                    print(f"stopped\t{result.stopped_reason}")
                    print(f"incomplete\t{result.incomplete}")
                    if result.error:
                        print(f"error\t{result.error}")
                    print(f"total_cost_usd\t{result.total_cost_usd:.6f}")
                    print(f"events\t{len([e for e in result.events if not e.skipped])}")
                    if not args.no_eval:
                        _write_eval(session, result, replay_path=args.replay_path)
    finally:
        await llm.aclose()
        for client in (clients.edgar, clients.newsroom):
            close = getattr(client, "close", None)
            if callable(close):
                close()

    return 1 if result is not None and result.stopped_reason == "error" else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
