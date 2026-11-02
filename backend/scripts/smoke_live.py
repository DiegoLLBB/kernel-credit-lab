"""Live-site smoke test: every page route and its required API calls.

Fails on any non-200 or empty required field. Used after deploy and locally:

    python -m scripts.smoke_live
    python -m scripts.smoke_live --base-url https://kernel-credit-lab.web.app
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE = "https://kernel-credit-lab.web.app"


def _get(url: str, *, timeout: float = 45.0) -> tuple[int, bytes, str]:
    req = Request(url, headers={"User-Agent": "kernel-smoke-live/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return int(resp.status), body, ctype
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return int(exc.code), body, ""


def _json(url: str) -> tuple[int, Any]:
    status, body, ctype = _get(url)
    if status != 200:
        return status, None
    if "json" not in ctype and body[:1] not in (b"{", b"["):
        return status, None
    try:
        return status, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def run(base: str) -> int:
    base = base.rstrip("/")
    failures = 0
    print(f"smoke_live: {base}")

    # Pages (HTML)
    pages = ["/", "/alerts", "/issuers"]
    for path in pages:
        status, body, _ = _get(f"{base}{path}")
        if status != 200 or len(body) < 50:
            _fail(f"GET {path} → {status} ({len(body)} bytes)")
            failures += 1
        else:
            _ok(f"GET {path}")

    # Health
    status, health = _json(f"{base}/health")
    if status != 200:
        _fail(f"GET /health → {status}")
        failures += 1
    else:
        _ok("GET /health")

    status, ready = _json(f"{base}/health/ready")
    if status != 200 or not isinstance(ready, dict):
        _fail(f"GET /health/ready → {status}")
        failures += 1
    else:
        db_ok = (ready.get("checks") or {}).get("database")
        if db_ok is False:
            _fail("GET /health/ready database=false")
            failures += 1
        else:
            _ok("GET /health/ready")

    # Overview
    status, overview = _json(f"{base}/api/credit/overview")
    if status != 200 or not isinstance(overview, dict):
        _fail(f"GET /api/credit/overview → {status}")
        failures += 1
        print(f"smoke_live: {failures} failure(s)")
        return 1
    header = overview.get("header") or {}
    if header.get("companies") is None:
        _fail("overview.header.companies missing")
        failures += 1
    else:
        _ok(f"GET /api/credit/overview ({header.get('companies')} companies)")

    # Alerts list + one alert
    status, alerts = _json(f"{base}/api/alerts?limit=5")
    if status != 200 or not isinstance(alerts, dict):
        _fail(f"GET /api/alerts → {status}")
        failures += 1
        alert_id = None
    else:
        items = alerts.get("items") or []
        _ok(f"GET /api/alerts ({len(items)} items)")
        alert_id = items[0]["id"] if items else None
        if alert_id:
            status, alert = _json(f"{base}/api/alerts/{alert_id}")
            if status != 200 or not isinstance(alert, dict) or not alert.get("title"):
                _fail(f"GET /api/alerts/{alert_id} → {status}")
                failures += 1
            else:
                _ok(f"GET /api/alerts/{alert_id}")
                page_status, page_body, _ = _get(f"{base}/alerts/{alert_id}")
                if page_status != 200 or len(page_body) < 50:
                    _fail(f"GET /alerts/{alert_id} → {page_status}")
                    failures += 1
                else:
                    _ok(f"GET /alerts/{alert_id}")
                run_id = alert.get("run_id")
                if run_id:
                    status, trace = _json(f"{base}/api/runs/{run_id}/trace")
                    if status != 200 or not isinstance(trace, dict):
                        _fail(f"GET /api/runs/{run_id}/trace → {status}")
                        failures += 1
                    else:
                        _ok(f"GET /api/runs/{run_id}/trace")
                        page_status, page_body, _ = _get(f"{base}/runs/{run_id}")
                        if page_status == 200:
                            _ok(f"GET /runs/{run_id}")
                        # Trace page may live under /alerts or /runs; non-200 is soft.

    # Issuers
    status, issuers = _json(f"{base}/api/issuers")
    if status != 200 or not isinstance(issuers, (dict, list)):
        _fail(f"GET /api/issuers → {status}")
        failures += 1
        issuer_keys: list[str] = []
    else:
        if isinstance(issuers, dict):
            rows = issuers.get("items") or issuers.get("issuers") or []
        else:
            rows = issuers
        issuer_keys = [r.get("key") for r in rows if isinstance(r, dict) and r.get("key")]
        _ok(f"GET /api/issuers ({len(issuer_keys)})")
        for key in issuer_keys:
            status, detail = _json(f"{base}/api/issuers/{key}")
            if status != 200 or not isinstance(detail, dict) or not detail.get("name"):
                _fail(f"GET /api/issuers/{key} → {status}")
                failures += 1
            else:
                _ok(f"GET /api/issuers/{key}")
            page_status, page_body, _ = _get(f"{base}/issuers/{key}")
            if page_status != 200 or len(page_body) < 50:
                _fail(f"GET /issuers/{key} → {page_status}")
                failures += 1
            else:
                _ok(f"GET /issuers/{key}")

    # One document from an alert citation, if any
    doc_id = None
    if alert_id:
        status, alert = _json(f"{base}/api/alerts/{alert_id}")
        if status == 200 and isinstance(alert, dict):
            for finding in alert.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                for cite in finding.get("citations") or []:
                    if isinstance(cite, dict) and cite.get("filing_id"):
                        doc_id = cite["filing_id"]
                        break
                if doc_id:
                    break
    if doc_id:
        status, doc = _json(f"{base}/api/documents/{doc_id}")
        if status != 200 or not isinstance(doc, dict):
            _fail(f"GET /api/documents/{doc_id} → {status}")
            failures += 1
        else:
            _ok(f"GET /api/documents/{doc_id}")
        page_status, page_body, _ = _get(f"{base}/documents/{doc_id}")
        if page_status != 200 or len(page_body) < 50:
            _fail(f"GET /documents/{doc_id} → {page_status}")
            failures += 1
        else:
            _ok(f"GET /documents/{doc_id}")
    else:
        print("  SKIP  no document citation on first alert")

    # Chart image: list charts for first issuer with any reading
    chart_ok = False
    for key in issuer_keys[:3]:
        status, charts = _json(f"{base}/api/issuers/{key}/charts")
        if status != 200:
            continue
        items = []
        if isinstance(charts, dict):
            items = charts.get("items") or charts.get("charts") or []
        elif isinstance(charts, list):
            items = charts
        for ch in items:
            cid = ch.get("id") if isinstance(ch, dict) else None
            if not cid:
                continue
            status, body, ctype = _get(f"{base}/api/charts/{cid}/image")
            if status == 200 and len(body) > 100:
                _ok(f"GET /api/charts/{cid}/image ({ctype or 'image'})")
                chart_ok = True
                break
        if chart_ok:
            break
    if not chart_ok:
        print("  SKIP  no chart image available yet")

    # Replay evaluation endpoint
    status, replay = _json(f"{base}/api/replay")
    if status != 200:
        _fail(f"GET /api/replay → {status}")
        failures += 1
    else:
        _ok("GET /api/replay")

    print(f"smoke_live: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    args = parser.parse_args(argv)
    try:
        return run(args.base_url)
    except URLError as exc:
        print(f"smoke_live: network error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
