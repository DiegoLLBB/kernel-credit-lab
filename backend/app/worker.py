"""Background worker (ARQ). Ingestion and research runs execute here."""

import logging

from arq.connections import RedisSettings

from app.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("kernel.worker")


async def ping(ctx: dict) -> str:
    logger.info("worker_ping")
    return "pong"


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 900
