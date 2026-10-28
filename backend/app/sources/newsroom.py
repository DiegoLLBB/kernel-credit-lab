"""Issuer newsroom client for press-release ingestion (alongside EDGAR).

Uses the same declared User-Agent as EDGAR (``SEC_USER_AGENT``), rate-limits to
≤ 2 requests/second, retries on 429/5xx, and checks robots.txt once per host.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Filing, Issuer
from app.sources.edgar import _require_user_agent, html_to_text

MAX_RPS = 2.0
FORM_PRESS_RELEASE = "press_release"
_DEFAULT_NEWSROOMS = Path(__file__).resolve().parents[2] / "seed" / "newsrooms.yaml"


class NewsroomError(RuntimeError):
    """Newsroom request, robots, or configuration failure."""


@dataclass(frozen=True)
class NewsroomConfig:
    listing_url: str
    rss_url: str | None = None


@dataclass(frozen=True)
class ReleaseRef:
    title: str
    published: date
    url: str


def default_newsrooms_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    if s.newsrooms_path:
        return Path(s.newsrooms_path)
    return _DEFAULT_NEWSROOMS


def load_newsrooms(path: Path | None = None) -> dict[str, NewsroomConfig]:
    """Load per-issuer newsroom config from YAML. Missing file → empty map."""
    cfg_path = path or default_newsrooms_path()
    if not cfg_path.is_file():
        return {}
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    issuers = raw.get("issuers") or {}
    out: dict[str, NewsroomConfig] = {}
    for key, block in issuers.items():
        if not isinstance(block, dict):
            continue
        listing = (block.get("listing_url") or "").strip()
        if not listing:
            continue
        rss = (block.get("rss_url") or "").strip() or None
        out[str(key)] = NewsroomConfig(listing_url=listing, rss_url=rss)
    return out


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _path_for_robots(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _parse_rss_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_rss_items(xml_text: str) -> list[ReleaseRef]:
    """Parse an RSS 2.0 channel into ReleaseRef rows."""
    root = ET.fromstring(xml_text)
    items: list[ReleaseRef] = []
    for node in root.iter():
        if _local(node.tag) != "item":
            continue
        title = link = pub = ""
        for child in list(node):
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                title = text
            elif name == "link":
                link = text
            elif name == "pubDate":
                pub = text
        published = _parse_rss_date(pub)
        if title and link and published:
            items.append(ReleaseRef(title=title, published=published, url=link))
    return items


def parse_listing_html(html: str, base_url: str) -> list[ReleaseRef]:
    """Best-effort extract of title/url/date links from a static HTML listing.

    JS-rendered category pages (e.g. Verizon financial) often yield nothing; prefer RSS.
    """
    refs: list[ReleaseRef] = []
    # <time datetime="2026-08-20">…</time> near an <a href="…">
    pattern = re.compile(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<time[^>]*datetime="([^"]+)"',
        flags=re.I | re.S,
    )
    for match in pattern.finditer(html):
        href, label, dt = match.group(1), match.group(2), match.group(3)
        title = re.sub(r"<[^>]+>", "", label)
        title = re.sub(r"\s+", " ", title).strip()
        published = _parse_rss_date(dt) or _parse_rss_date(dt[:10])
        if not title or not published:
            continue
        url = urljoin(base_url, href)
        refs.append(ReleaseRef(title=title, published=published, url=url))
    # Dedupe by URL, keep first
    seen: set[str] = set()
    out: list[ReleaseRef] = []
    for ref in refs:
        if ref.url in seen:
            continue
        seen.add(ref.url)
        out.append(ref)
    return out


class NewsroomClient:
    """Synchronous issuer-newsroom client with robots checks and retries."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        newsrooms: dict[str, NewsroomConfig] | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ) -> None:
        self.settings = settings or get_settings()
        self._user_agent = _require_user_agent(self.settings.sec_user_agent)
        self._newsrooms = (
            newsrooms
            if newsrooms is not None
            else load_newsrooms(default_newsrooms_path(self.settings))
        )
        self._clock = clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._min_interval = 1.0 / MAX_RPS
        self._last_request_at = 0.0
        self._robots: dict[str, RobotFileParser] = {}
        self._owns_client = client is None
        self._http = client or httpx.Client(
            transport=transport,
            headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=60.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> NewsroomClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def config_for(self, issuer: str) -> NewsroomConfig:
        key = issuer.strip().lower()
        cfg = self._newsrooms.get(key)
        if cfg is None:
            raise NewsroomError(f"no newsroom configured for issuer {issuer!r}")
        return cfg

    def _throttle(self) -> None:
        now = self._clock()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            self._sleeper(wait)

    def _robots_for(self, url: str) -> RobotFileParser:
        host = _host(url)
        cached = self._robots.get(host)
        if cached is not None:
            return cached
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            self._throttle()
            self._last_request_at = self._clock()
            resp = self._http.get(robots_url)
            if resp.status_code >= 400:
                # Fail open only for missing robots; still record an empty parser that allows all.
                parser.parse([])
            else:
                parser.parse(resp.text.splitlines())
        except httpx.TransportError:
            parser.parse([])
        self._robots[host] = parser
        return parser

    def _assert_allowed(self, url: str) -> None:
        if urlparse(url).path.rstrip("/").endswith("robots.txt"):
            return
        parser = self._robots_for(url)
        if not parser.can_fetch(self._user_agent, url):
            raise NewsroomError(f"robots.txt disallows {url}")

    def _get(self, url: str) -> httpx.Response:
        self._assert_allowed(url)
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            self._last_request_at = self._clock()
            try:
                resp = self._http.get(url)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise NewsroomError(f"newsroom transport error for {url}: {exc}") from exc
                self._sleeper(min(2**attempt, 30))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self._max_retries:
                    raise NewsroomError(
                        f"newsroom {resp.status_code} for {url} after {attempt} attempts"
                    )
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                self._sleeper(min(delay, 60))
                continue

            if resp.status_code >= 400:
                raise NewsroomError(f"newsroom {resp.status_code} for {url}: {resp.text[:200]}")
            return resp

    def list_releases(self, issuer: str, since: date, until: date) -> list[ReleaseRef]:
        """Return press releases for ``issuer`` with published date in [since, until]."""
        if since > until:
            raise ValueError("since must be ≤ until")
        cfg = self.config_for(issuer)
        refs: list[ReleaseRef] = []
        if cfg.rss_url:
            xml_text = self._get(cfg.rss_url).text
            refs.extend(parse_rss_items(xml_text))
        else:
            html = self._get(cfg.listing_url).text
            refs.extend(parse_listing_html(html, cfg.listing_url))

        out = [r for r in refs if since <= r.published <= until]
        out.sort(key=lambda r: (r.published, r.url), reverse=True)
        return out

    def fetch_text(self, url: str) -> str:
        """Download article HTML and return plain text (table rows stay one line)."""
        return html_to_text(self._get(url).text)

    def upsert_releases(
        self, session: Session, issuer: Issuer, releases: list[ReleaseRef]
    ) -> list[Filing]:
        """Insert or update ``Filing`` rows as press releases; idempotent on url.

        Does not set ``content_hash`` — filled by ``fetch_release_text``.
        """
        result: list[Filing] = []
        for ref in releases:
            row = session.scalars(select(Filing).where(Filing.url == ref.url)).first()
            if row is None:
                row = Filing(url=ref.url, accession_number=None)
                session.add(row)
            row.issuer_id = issuer.id
            row.form_type = FORM_PRESS_RELEASE
            row.filed_at = ref.published
            row.url = ref.url
            row.title = ref.title
            result.append(row)
        session.flush()
        return result

    def fetch_release_text(self, session: Session, filing: Filing) -> str:
        """Fetch press-release text, set ``content_hash`` to sha256 of the text, return text."""
        if not filing.url:
            raise NewsroomError("Filing has no url to fetch")
        text = self.fetch_text(filing.url)
        filing.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        session.add(filing)
        session.flush()
        return text
