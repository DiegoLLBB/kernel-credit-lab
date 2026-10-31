"""Tests for the SEC companyfacts XBRL client (offline; fixtures byte-for-byte)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.sources import xbrl
from app.sources.edgar import EdgarError
from app.sources.xbrl import XbrlClient, fixture_path, pad_cik, xbrl_url

UA = "KernelCreditLab-tests/1.0 contact kernel-tests@kernelcreditlab.dev"
FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
DUMMY_CIK = "0000001234"


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_url_zero_pads_cik():
    assert xbrl_url("1341439") == ("https://data.sec.gov/api/xbrl/companyfacts/CIK0001341439.json")
    assert xbrl_url("0000732712").endswith("CIK0000732712.json")


def test_user_agent_required():
    with pytest.raises(EdgarError, match="SEC_USER_AGENT is required"):
        XbrlClient(user_agent="").fetch_body(DUMMY_CIK)


def test_user_agent_needs_contact_email():
    with pytest.raises(EdgarError, match="contact email"):
        XbrlClient(user_agent="not-a-mailto-agent").fetch_body(DUMMY_CIK)


def test_sends_configured_user_agent_and_accept():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["User-Agent"]
        seen["accept"] = request.headers["Accept"]
        return httpx.Response(200, content=b'{"cik":"0001341439","facts":{}}')

    client = XbrlClient(user_agent=UA, transport=_transport(handler), sleeper=lambda s: None)
    body = client.fetch_body(DUMMY_CIK)
    assert json.loads(body)["cik"] == "0001341439"
    assert seen["ua"] == UA
    assert seen["accept"] == "application/json"


def test_retry_honors_retry_after_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, content=b"slow down")
        return httpx.Response(200, content=b'{"cik":"x","facts":{}}')

    client = XbrlClient(
        user_agent=UA,
        transport=_transport(handler),
        sleeper=sleeps.append,
        min_interval=0.0,
    )
    assert json.loads(client.fetch_body(DUMMY_CIK))["cik"] == "x"
    assert calls["n"] == 2
    assert sleeps and sleeps[0] == pytest.approx(7.0)


def test_gives_up_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"busy")

    client = XbrlClient(
        user_agent=UA, transport=_transport(handler), sleeper=lambda s: None, min_interval=0.0
    )
    with pytest.raises(httpx.HTTPError):
        client.fetch_body(DUMMY_CIK)


def test_non_retryable_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    client = XbrlClient(
        user_agent=UA, transport=_transport(handler), sleeper=lambda s: None, min_interval=0.0
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_body(DUMMY_CIK)


def test_record_script_refuses_without_live_flag(monkeypatch):
    from scripts.record_xbrl_fixtures import main

    monkeypatch.delenv("XBRL_LIVE", raising=False)
    assert main() == 2


# --- Fixture contract tests (recorded live 2026-10-31; never hand-edited). ---


def _load(cik: str) -> dict:
    path = FIXTURES / f"CIK{pad_cik(cik)}.json"
    assert path.exists(), f"missing fixture {path.name}"
    return json.loads(path.read_bytes())


def test_fixture_path_names():
    assert fixture_path("1341439") == "tests/fixtures/xbrl/CIK0001341439.json"


@pytest.mark.parametrize(
    "cik",
    ["0001326160", "0000017797", "1341439", "0000732712", "0000012927", "0000050863", "1018724"],
)
def test_fact_fixtures_have_us_gaap_facts(cik):
    doc = _load(cik)
    assert "facts" in doc and "us-gaap" in doc["facts"], f"{cik} fixture has no us-gaap facts"


@pytest.mark.parametrize("cik,entity", [("0000030371", "CAROLINAS"), ("0000037637", "")])
def test_subsidiary_fixtures_have_no_us_gaap_facts(cik, entity):
    """Duke Carolinas/Florida file no us-gaap facts live; metrics must be missing."""
    doc = _load(cik)
    assert "us-gaap" not in doc.get("facts", {}), f"{cik} unexpectedly gained us-gaap facts"
    assert entity.upper() in doc.get("entityName", "").upper()


def test_fixture_documents_carry_filed_dates():
    """Point-in-time visibility needs ``filed`` on datapoints."""
    doc = _load("0001341439")
    units = doc["facts"]["us-gaap"]["Assets"]["units"]
    datapoints = next(iter(units.values()))
    assert datapoints and all("filed" in d and "accn" in d for d in datapoints[:50])


def test_client_round_trips_fixture_bytes():
    """fetch_body returns response bytes unchanged (the byte-for-byte property)."""
    raw = (FIXTURES / "CIK0001341439.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw)

    client = XbrlClient(user_agent=UA, transport=_transport(handler), sleeper=lambda s: None)
    assert client.fetch_body(DUMMY_CIK) == raw
    assert client.company_facts("0001341439")["entityName"] == "Oracle Corporation"


def test_module_exports_rate_limit_constants():
    assert xbrl.MAX_RETRIES == 5
    assert 429 in xbrl.RETRYABLE and 404 not in xbrl.RETRYABLE
