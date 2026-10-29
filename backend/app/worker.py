"""Background worker (ARQ). Ingestion and research runs execute here."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.core.logging import configure_logging
from app.db import SessionLocal
from app.ingest.replay import default_clients, load_replay_events, step_to_next

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("kernel.worker")


async def ping(ctx: dict) -> str:
    logger.info("worker_ping")
    return "pong"


async def replay_tick(ctx: dict, as_of: str) -> dict[str, Any]:
    """Advance one eventful day at a time up to ``as_of`` (ISO date).

    Walks calendar days from the current replay cursor and stops at the first day
    that creates pending Event rows, so the agent can update instrument status
    before later days (especially maturities) are opened. A multi-day jump would
    create maturity events from stale instrument status.
    """
    until = date.fromisoformat(as_of)
    clients = default_clients()
    try:
        with SessionLocal() as session:
            load_replay_events(session)
            created = step_to_next(session, until, clients)
            session.commit()
            keys = [e.replay_key for e in created]
            landed = created[0].event_date.isoformat() if created else None
        logger.info("replay_tick until=%s landed=%s created=%s", as_of, landed, len(keys))
        return {
            "as_of": as_of,
            "landed": landed,
            "created": len(keys),
            "replay_keys": keys,
        }
    finally:
        for client in (clients.edgar, clients.newsroom):
            close = getattr(client, "close", None)
            if callable(close):
                close()


class WorkerSettings:
    functions = [ping, replay_tick]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 900
