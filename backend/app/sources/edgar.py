"""SEC EDGAR client for listing filings, reading indexes and extracting text.

Uses httpx with a declared User-Agent from settings (``SEC_USER_AGENT``). Rate-limited
to ≤ 8 requests/second; retries on 429/5xx with backoff. See SEC fair-access policy.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Filing, Issuer

DATA_SEC = "https://data.sec.gov"
WWW_SEC = "https://www.sec.gov"
MAX_RPS = 8.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RESERVED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


class EdgarError(RuntimeError):
    """EDGAR request or configuration failure."""


@dataclass(frozen=True)
class FilingRef:
    accession: str
    form: str
    filing_date: date
    primary_document: str
    url: str
    cik: str


@dataclass(frozen=True)
class DocumentRef:
    name: str
    type: str
    url: str


def pad_cik(cik: str | int) -> str:
    digits = re.sub(r"\D", "", str(cik))
    if not digits or len(digits) > 10:
        raise ValueError(f"invalid CIK: {cik!r}")
    return digits.zfill(10)


def bare_cik(cik: str | int) -> str:
    return pad_cik(cik).lstrip("0") or "0"


def accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def archives_base(cik: str | int, accession: str) -> str:
    return f"{WWW_SEC}/Archives/edgar/data/{bare_cik(cik)}/{accession_nodash(accession)}"


def primary_document_url(cik: str | int, accession: str, primary_document: str) -> str:
    return f"{archives_base(cik, accession)}/{primary_document.lstrip('/')}"


def index_url(cik: str | int, accession: str) -> str:
    return f"{archives_base(cik, accession)}/{accession}-index.htm"


def _require_user_agent(user_agent: str) -> str:
    ua = (user_agent or "").strip()
    if not ua:
        raise EdgarError("SEC_USER_AGENT is required (e.g. 'Kernel Credit Lab you@domain.com')")
    match = _EMAIL_RE.search(ua)
    if match is None:
        raise EdgarError("SEC_USER_AGENT must include a contact email address")
    domain = match.group(0).rsplit("@", 1)[1].lower()
    if domain in _RESERVED_EMAIL_DOMAINS:
        raise EdgarError(
            "SEC_USER_AGENT email must not use a reserved example.com/.org/.net domain"
        )
    return ua


def _zip_recent(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn EDGAR columnar `recent` / overflow arrays into row dicts."""
    if not block or "accessionNumber" not in block:
        return []
    keys = list(block.keys())
    n = len(block["accessionNumber"])
    rows: list[dict[str, Any]] = []
    for i in range(n):
        rows.append({k: block[k][i] for k in keys if isinstance(block[k], list)})
    return rows


class _IndexParser(HTMLParser):
    """Parse an EDGAR ``*-index.htm`` document table into DocumentRef rows."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.rows: list[DocumentRef] = []
        self._in_tr = False
        self._in_td = False
        self._cells: list[tuple[str, str | None]] = []
        self._cell = ""
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if tag == "tr":
            self._in_tr = True
            self._cells = []
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._cell = ""
            self._href = None
        elif tag == "a" and self._in_td:
            self._href = ad.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            self._cells.append((self._cell.strip(), self._href))
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            self._finish_row()
            self._in_tr = False

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._cell += data

    def _finish_row(self) -> None:
        if len(self._cells) < 4:
            return
        # seq, description, document(+href), type, [size]
        _seq, (desc, _), (doc_text, href), (doc_type, _) = self._cells[:4]
        name = (doc_text.split()[0] if doc_text else "") or ""
        if not href or not name:
            return
        if name.lower() in {"document", "file"}:
            return
        url = self._absolute(href, name)
        self.rows.append(DocumentRef(name=name, type=(doc_type or desc or "").strip(), url=url))

    def _absolute(self, href: str, name: str) -> str:
        if href.startswith("/ix?doc="):
            path = href.split("doc=", 1)[1]
            return urljoin(WWW_SEC, path)
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return urljoin(WWW_SEC, href)
        return f"{self.base_url.rstrip('/')}/{name}"


class _TextExtractor(HTMLParser):
    """HTML → plain text; each table row becomes a single line."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._in_tr = False
        self._in_td = False
        self._row_cells: list[str] = []
        self._cell = ""
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "tr":
            self._in_tr = True
            self._row_cells = []
        elif tag in {"td", "th"} and self._in_tr:
            self._in_td = True
            self._cell = ""
        elif (
            tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}
            and not self._in_tr
            and self.parts
            and not self.parts[-1].endswith("\n")
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False
            return
        if tag in {"td", "th"} and self._in_td:
            self._row_cells.append(re.sub(r"\s+", " ", self._cell).strip())
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            line = " | ".join(c for c in self._row_cells if c)
            if line:
                self.parts.append(line + "\n")
            self._in_tr = False
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4"} and not self._in_tr:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_td:
            self._cell += data
        elif not self._in_tr:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class EdgarClient:
    """Synchronous EDGAR client with rate limiting and retries."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ) -> None:
        self.settings = settings or get_settings()
        self._user_agent = _require_user_agent(self.settings.sec_user_agent)
        self._clock = clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._min_interval = 1.0 / MAX_RPS
        self._last_request_at = 0.0
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

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        now = self._clock()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            self._sleeper(wait)

    def _get(self, url: str) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            self._last_request_at = self._clock()
            try:
                resp = self._http.get(url)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise EdgarError(f"EDGAR transport error for {url}: {exc}") from exc
                self._sleeper(min(2**attempt, 30))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self._max_retries:
                    raise EdgarError(f"EDGAR {resp.status_code} for {url} after {attempt} attempts")
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                self._sleeper(min(delay, 60))
                continue

            if resp.status_code >= 400:
                raise EdgarError(f"EDGAR {resp.status_code} for {url}: {resp.text[:200]}")
            return resp

    def _get_json(self, url: str) -> dict[str, Any]:
        return self._get(url).json()

    def list_filings(
        self,
        cik: str | int,
        forms: list[str] | tuple[str, ...] | set[str],
        since: date,
        until: date,
    ) -> list[FilingRef]:
        """Return filings for ``cik`` matching ``forms`` with filing_date in [since, until]."""
        if since > until:
            raise ValueError("since must be ≤ until")
        form_set = {f.upper() for f in forms}
        cik10 = pad_cik(cik)
        main = self._get_json(f"{DATA_SEC}/submissions/CIK{cik10}.json")
        blocks: list[dict[str, Any]] = [main.get("filings", {}).get("recent") or {}]

        for meta in main.get("filings", {}).get("files") or []:
            name = meta.get("name")
            if not name:
                continue
            filing_from = meta.get("filingFrom")
            filing_to = meta.get("filingTo")
            # Skip overflow pages that cannot intersect [since, until].
            if filing_to and date.fromisoformat(str(filing_to)) < since:
                continue
            if filing_from and date.fromisoformat(str(filing_from)) > until:
                continue
            blocks.append(self._get_json(f"{DATA_SEC}/submissions/{name}"))

        out: list[FilingRef] = []
        seen: set[str] = set()
        for block in blocks:
            for row in _zip_recent(block):
                form = str(row.get("form") or "").upper()
                if form_set and form not in form_set:
                    continue
                filing_date_s = row.get("filingDate")
                if not filing_date_s:
                    continue
                filing_date = date.fromisoformat(str(filing_date_s))
                if filing_date < since or filing_date > until:
                    continue
                accession = str(row["accessionNumber"])
                if accession in seen:
                    continue
                primary = str(row.get("primaryDocument") or "")
                if not primary:
                    continue
                seen.add(accession)
                out.append(
                    FilingRef(
                        accession=accession,
                        form=form,
                        filing_date=filing_date,
                        primary_document=primary,
                        url=primary_document_url(cik10, accession, primary),
                        cik=cik10,
                    )
                )
        out.sort(key=lambda f: (f.filing_date, f.accession), reverse=True)
        return out

    def filing_documents(self, cik: str | int, accession: str) -> list[DocumentRef]:
        """Parse the filing index page into document name/type/url rows."""
        url = index_url(cik, accession)
        html = self._get(url).text
        parser = _IndexParser(archives_base(cik, accession))
        parser.feed(html)
        return parser.rows

    def fetch_text(self, url: str) -> str:
        """Download HTML (or SGML-wrapped HTML) and return plain text."""
        raw = self._get(url).text
        # EDGAR often wraps exhibits in <DOCUMENT>…<TEXT>…</TEXT>
        match = re.search(r"<TEXT>(.*)</TEXT>", raw, flags=re.I | re.S)
        html = match.group(1) if match else raw
        return html_to_text(html)

    def upsert_filings(
        self, session: Session, issuer: Issuer, filings: list[FilingRef]
    ) -> list[Filing]:
        """Insert or update ``Filing`` rows; idempotent on accession_number.

        Does not set ``content_hash`` — that is filled by ``fetch_filing_text``.
        """
        result: list[Filing] = []
        for ref in filings:
            row = session.scalars(
                select(Filing).where(Filing.accession_number == ref.accession)
            ).first()
            if row is None:
                row = Filing(accession_number=ref.accession, url=ref.url)
                session.add(row)
            row.issuer_id = issuer.id
            row.form_type = ref.form
            row.filed_at = ref.filing_date
            row.url = ref.url
            row.title = ref.primary_document
            result.append(row)
        session.flush()
        return result

    def fetch_filing_text(self, session: Session, filing: Filing) -> str:
        """Fetch filing body text, set ``content_hash`` to sha256 of the text, return text."""
        if not filing.url:
            raise EdgarError("Filing has no url to fetch")
        text = self.fetch_text(filing.url)
        filing.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        session.add(filing)
        session.flush()
        return text
