"""Record FRED observation fixtures. Requires FRED_LIVE=1 and FRED_API_KEY.

Writes response JSON only. Strips api_key from any companion URL sidecar; the JSON
body from FRED does not contain the key.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import get_settings
from app.sources.fred import FredClient, FredError, redact_api_key

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fred"

SERIES = ("DGS5", "DEXUSEU")
START = date(2026, 1, 1)
END = date(2026, 10, 31)


def _strip_key_from_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != "api_key"
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _load_dotenv_candidates() -> None:
    """Populate os.environ from nearby .env files without overriding existing vars."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / ".env", here.parents[2] / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def main() -> int:
    if os.getenv("FRED_LIVE") != "1":
        print("Refusing to run: set FRED_LIVE=1 to hit api.stlouisfed.org", file=sys.stderr)
        return 2
    _load_dotenv_candidates()
    get_settings.cache_clear()
    settings = get_settings()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    try:
        with FredClient(settings=settings) as client:
            for series_id in SERIES:
                url = client._observations_url(  # noqa: SLF001 — recording helper
                    series_id, start=START, end=END, sort_order="asc"
                )
                resp = client._get(url)  # noqa: SLF001
                # Ensure nothing we save embeds the key.
                body_text = redact_api_key(resp.text)
                payload = json.loads(body_text)
                name = f"{series_id}_{START.isoformat()}_{END.isoformat()}.json"
                path = FIXTURES / name
                path.write_text(json.dumps(payload, indent=2) + "\n")
                meta = FIXTURES / f"{series_id}_{START.isoformat()}_{END.isoformat()}.source.txt"
                meta.write_text(_strip_key_from_url(url) + "\n")
                print(f"wrote {path.name} ({path.stat().st_size} bytes) ← {redact_api_key(url)}")
    except FredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
