import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

integration = pytest.mark.skipif(
    os.getenv("KERNEL_INTEGRATION") != "1",
    reason="needs Postgres + Redis (set KERNEL_INTEGRATION=1; runs in CI)",
)


def test_liveness():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers["x-request-id"]


def test_request_id_is_propagated():
    resp = client.get("/health", headers={"x-request-id": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"


@integration
def test_readiness_with_dependencies():
    resp = client.get("/health/ready")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["checks"] == {"database": True, "redis": True}


@integration
def test_readiness_skips_redis_when_queue_disabled(monkeypatch):
    """Production has no Redis and nothing uses the queue: redis must not gate readiness."""
    from app.api import health
    from app.config import Settings

    monkeypatch.setattr(health, "get_settings", lambda: Settings(queue_enabled=False))
    resp = client.get("/health/ready")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["checks"] == {"database": True}


@integration
def test_runs_endpoints_against_migrated_schema():
    runs = client.get("/api/runs")
    calls = client.get("/api/model-calls")
    assert runs.status_code == 200 and isinstance(runs.json(), list)
    assert calls.status_code == 200 and isinstance(calls.json(), list)
    assert client.get("/api/runs/does-not-exist").status_code == 404
