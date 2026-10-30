"""Replay window status and latest evaluation summary."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import ReplayEvalSummary, ReplayStatusOut
from app.db import get_session
from app.ingest.replay import DEFAULT_REPLAY_PATH
from app.models import Event
from app.replay.runner import DEFAULT_RUNS_DIR

router = APIRouter(prefix="/api/replay", tags=["replay"])
DbSession = Annotated[Session, Depends(get_session)]


def _window_from_replay_json(path: Path) -> tuple[date | None, date | None]:
    if not path.is_file():
        return None, None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    window = meta.get("window") or {}
    start = window.get("from")
    end = window.get("to")
    try:
        return (
            date.fromisoformat(str(start)) if start else None,
            date.fromisoformat(str(end)) if end else None,
        )
    except ValueError:
        return None, None


def _latest_eval_summary(runs_dir: Path) -> ReplayEvalSummary | None:
    if not runs_dir.is_dir():
        return None
    # Prefer the stable ``latest_eval.json`` written by ``run_replay --eval-latest``.
    preferred = runs_dir / "latest_eval.json"
    if preferred.is_file():
        path = preferred
    else:
        candidates = sorted(
            runs_dir.glob("*_eval.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not candidates:
            return None
        path = candidates[0]
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    alert = data.get("alert") or {}
    totals = data.get("totals") or {}
    cost = totals.get("cost_usd")
    if cost is None:
        cost = totals.get("total_cost_usd")
    return ReplayEvalSummary(
        precision=alert.get("precision"),
        recall=alert.get("recall"),
        incomplete=data.get("incomplete"),
        cost_usd=float(cost) if cost is not None else None,
        source_path=str(path),
        evaluated_at=data.get("evaluated_at") or data.get("generated_at"),
    )


@router.get("", response_model=ReplayStatusOut)
def get_replay_status(session: DbSession) -> ReplayStatusOut:
    window_from, window_until = _window_from_replay_json(DEFAULT_REPLAY_PATH)
    rows = session.execute(select(Event.status, func.count()).group_by(Event.status)).all()
    counts = {str(status): int(n) for status, n in rows}
    return ReplayStatusOut(
        window_from=window_from,
        window_until=window_until,
        event_counts_by_status=counts,
        latest_eval=_latest_eval_summary(DEFAULT_RUNS_DIR),
    )
