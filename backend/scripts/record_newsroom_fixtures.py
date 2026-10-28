"""Record newsroom fixtures byte-for-byte. Requires NEWSROOM_LIVE=1 and SEC_USER_AGENT.

The only change made to a response body is `redact_credentials`: third-party access tokens
embedded in issuer pages (e.g. Verizon's Mapbox token) are replaced with a fixed placeholder,
so they are never committed. Article text is untouched.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from app.config import get_settings
from app.sources.newsroom import NewsroomClient, NewsroomError

# Mapbox public (pk.) and secret (sk.) tokens: <prefix>.<base64url payload>.<signature>
_TOKEN_RE = re.compile(rb"\b(?:pk|sk)\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
REDACTED = b"REDACTED-THIRD-PARTY-TOKEN"


def redact_credentials(body: bytes) -> bytes:
    """Replace embedded third-party access tokens with a fixed placeholder."""
    return _TOKEN_RE.sub(REDACTED, body)


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "newsroom"

SOURCES: list[tuple[str, str]] = [
    (
        "verizon_tender_launch_2026-05-11.html",
        "https://www.verizon.com/about/news/feed/verizon-debt-tender-offer-consent-solicitation",
    ),
    (
        "verizon_tender_results_2026-06-17.html",
        "https://www.verizon.com/about/node/929969",
    ),
    (
        "verizon_redemption_2026-08-20.html",
        "https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026",
    ),
    (
        "verizon_financial_news.xml",
        "https://www.verizon.com/about/rss/news/financial-news.xml",
    ),
    (
        "verizon_robots.txt",
        "https://www.verizon.com/robots.txt",
    ),
]


def main() -> int:
    if os.getenv("NEWSROOM_LIVE") != "1":
        print("Refusing to run: set NEWSROOM_LIVE=1 to hit verizon.com", file=sys.stderr)
        return 2
    get_settings.cache_clear()
    settings = get_settings()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    try:
        with NewsroomClient(settings=settings) as client:
            for name, url in SOURCES:
                # robots.txt itself: bypass assert by fetching via _http after allow check skip
                if name.endswith("robots.txt"):
                    client._throttle()
                    client._last_request_at = client._clock()
                    resp = client._http.get(url)
                    if resp.status_code >= 400:
                        raise NewsroomError(f"{resp.status_code} for {url}")
                else:
                    resp = client._get(url)
                path = FIXTURES / name
                body = redact_credentials(resp.content)
                path.write_bytes(body)
                print(f"wrote {path.name} ({len(body)} bytes) ← {url}")
    except NewsroomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
