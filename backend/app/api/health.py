"""Liveness and readiness endpoints."""

import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


async def _check_redis() -> bool:
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


def _check_db() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    """Readiness: dependencies reachable and model providers configured.

    Redis is only checked when the queue is enabled (QUEUE_ENABLED). Production
    has no Redis and nothing there uses the queue, so the check is skipped there;
    locally the queue is enabled and Redis stays a hard readiness dependency.
    """
    s = get_settings()
    checks: dict[str, bool] = {}
    for name, fn in (("database", _check_db),):
        try:
            checks[name] = fn()
        except Exception as exc:
            logger.warning("readiness_check_failed", extra={"check": name, "error": str(exc)})
            checks[name] = False
    if s.queue_enabled:
        try:
            checks["redis"] = await _check_redis()
        except Exception as exc:
            logger.warning("readiness_check_failed", extra={"check": "redis", "error": str(exc)})
            checks["redis"] = False

    providers = {
        "meta": bool(s.model_api_key and s.meta_model_api_base_url),
        "baseten": bool(s.baseten_api_key and s.baseten_base_url),
    }
    ok = all(checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks, "providers": providers}
