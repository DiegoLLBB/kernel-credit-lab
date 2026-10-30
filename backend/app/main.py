"""FastAPI application entrypoint."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, documents, health, issuers, replay, runs
from app.config import get_settings
from app.core.logging import configure_logging, request_id_var

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("kernel.api")

app = FastAPI(
    title="Kernel Credit Lab API",
    version="0.1.0",
    description="Agentic, verifiable research for USD investment-grade bonds.",
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:3000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["x-request-id"] = request_id
    if request.url.path not in ("/health",):
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
    return response


app.include_router(health.router)
app.include_router(runs.router)
app.include_router(alerts.router)
app.include_router(issuers.router)
app.include_router(documents.router)
app.include_router(replay.router)
