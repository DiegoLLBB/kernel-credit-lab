"""Replay driver: turn ``replay_events_2026.json`` into ``Event`` rows in date order.

Agent-visible fields from the replay file (and only these)
---------------------------------------------------------
- ``issuer`` (resolved to ``Issuer`` / ``issuer_id``)
- ``available_from`` (becomes ``Event.event_date``)
- ``sources`` entries with ``url``, ``form``, and ``filed`` — and only those with
  ``filed ≤ available_from``. Later-filed sources are confirming evidence on
  ``ReplayExpectation`` for evaluation, never attached to the agent-visible event.

Everything else in the replay file (``key``, ``event_type``, ``title``, ``event_date``,
``why``, ``notes``, ``expected_changes``, ``expected_alert``, ``sources[].description``)
goes only to ``ReplayExpectation`` (or is used to derive confirming sources) and must
never appear on ``Event`` / ``Filing`` rows the agent can see.

Stepping vs jumping
-------------------
``replay_tick`` and CLI ``--step`` advance one calendar day at a time and stop at each
day with pending events so the agent can update instrument status in between. A jump of
more than a day (plain ``--until`` / ``advance_to``) creates maturity events from stale
instrument status — bonds still marked ``active`` because intervening events were never
processed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import unescape
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    Event,
    EventFiling,
    Filing,
    Instrument,
    Issuer,
    RateObservation,
    ReplayExpectation,
)

logger = logging.getLogger("kernel.ingest.replay")

DEFAULT_REPLAY_PATH = Path(__file__).resolve().parents[2] / "seed" / "replay_events_2026.json"
AGENT_FORBIDDEN_FIELDS = frozenset(
    {"expected_changes", "expected_alert", "why", "notes", "replay_key", "key", "title"}
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class ReplayValidationError(ValueError):
    """Invalid replay seed file."""


class TextFetcher(Protocol):
    def fetch_text(self, url: str) -> str: ...


@dataclass
class ReplayClients:
    """Injected source clients (real or fakes for offline tests)."""

    edgar: TextFetcher
    newsroom: TextFetcher


@dataclass(frozen=True)
class ReplayWindow:
    start: date
    end: date


def _parse_date(value: Any, *, field: str, ctx: str) -> date:
    if not isinstance(value, str):
        raise ReplayValidationError(f"{ctx}: {field} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ReplayValidationError(f"{ctx}: invalid {field} {value!r}") from exc


def _require_https(url: Any, *, ctx: str) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ReplayValidationError(f"{ctx}: source url must be https (got {url!r})")
    return url


def _normalize_form(raw: str | None) -> str:
    text = (raw or "document").strip()
    return text or "document"


def _filing_form_type(form: str) -> str:
    lowered = form.strip().lower().replace(" ", "_")
    if lowered in {"press_release", "press-release", "pressrelease"}:
        return "press_release"
    return form.strip()


def _agent_event_type(form: str) -> str:
    """Derive agent-visible event_type from the primary source form only."""
    ft = _filing_form_type(form)
    if ft == "press_release":
        return "press_release"
    # SEC filings: surface the form itself (e.g. "8-K", "10-Q").
    return form.strip()


def _is_sec_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("sec.gov")


def _fridays_on_or_before(start: date, until: date) -> list[date]:
    """Inclusive Fridays in ``[start, until]``."""
    if until < start:
        return []
    first = start + timedelta(days=(4 - start.weekday()) % 7)
    out: list[date] = []
    cur = first
    while cur <= until:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def _format_day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


def extract_document_title(html: str) -> str | None:
    """Best-effort document title from HTML ``<title>`` (never replay metadata)."""
    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return title[:512] or None


def neutral_summary(*, issuer_name: str, form: str, filed: date) -> str:
    """Monitor-style one-liner from source metadata only."""
    ft = _filing_form_type(form)
    if ft == "press_release":
        return f"{issuer_name}, press release, {_format_day(filed)}"
    return f"{issuer_name}, {form.strip()} filed {_format_day(filed)}"


def _format_notional(amount: Decimal) -> str:
    """Compact USD notional for maturity summaries (e.g. ``$1.5bn``, ``$925m``)."""
    q = Decimal(amount)
    if q >= Decimal("1000000000"):
        bn = q / Decimal("1000000000")
        text = f"{bn:.10f}".rstrip("0").rstrip(".")
        return f"${text}bn"
    if q >= Decimal("1000000"):
        millions = q / Decimal("1000000")
        text = f"{millions:.10f}".rstrip("0").rstrip(".")
        return f"${text}m"
    return f"${q:,.0f}"


def _format_coupon(coupon: Decimal) -> str:
    """Bond coupon for summaries (3 dp; strips DB ``Numeric(10, 6)`` padding)."""
    return f"{Decimal(coupon):.3f}"


def maturity_summary(*, issuer_name: str, instrument: Instrument) -> str:
    """Agent-visible maturity line naming the instrument and notional."""
    if instrument.instrument_type == "loan" or instrument.rate_type == "floating":
        label = instrument.name
    else:
        label = f"{_format_coupon(instrument.coupon)}% Notes due {instrument.maturity.year}"
    amount = _format_notional(instrument.amount_outstanding)
    return f"{issuer_name}, {label} ({amount}) mature {_format_day(instrument.maturity)}"


def split_sources_by_availability(
    sources: list[dict[str, Any]], available_from: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Eligible (filed ≤ available_from) vs confirming (filed later).

    Invalid ``filed`` values raise ``ReplayValidationError`` (never bare ``ValueError``).
    """
    eligible: list[dict[str, Any]] = []
    confirming: list[dict[str, Any]] = []
    for src in sources:
        filed_s = src.get("filed")
        if not filed_s:
            eligible.append(src)
            continue
        try:
            filed = date.fromisoformat(str(filed_s))
        except ValueError as exc:
            raise ReplayValidationError(f"invalid filed date {filed_s!r}") from exc
        if filed <= available_from:
            eligible.append(src)
        else:
            confirming.append(src)
    return eligible, confirming


def resolve_issuer(session: Session, issuer_key: str) -> Issuer | None:
    return session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()


def load_replay_events(session: Session, path: Path | str | None = None) -> list[ReplayExpectation]:
    """Validate the replay JSON and upsert ``ReplayExpectation`` rows.

    Does not create ``Event`` rows. Seed ``event_type`` / ``title`` / ``event_date`` /
    ``why`` / ``notes`` / ``expected_*`` stay on ``ReplayExpectation`` only.
    """
    path = Path(path) if path is not None else DEFAULT_REPLAY_PATH
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ReplayValidationError("root: must be a JSON object")

    window_raw = data.get("window") or {}
    window = ReplayWindow(
        start=_parse_date(window_raw.get("from"), field="window.from", ctx="root"),
        end=_parse_date(window_raw.get("to"), field="window.to", ctx="root"),
    )

    events_raw = data.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ReplayValidationError("events: must be a non-empty list")

    keys: set[str] = set()
    prev_available: date | None = None
    errors: list[str] = []
    parsed: list[dict[str, Any]] = []

    for idx, raw in enumerate(events_raw):
        ctx = f"events[{idx}]"
        if not isinstance(raw, dict):
            errors.append(f"{ctx}: must be an object")
            continue
        key = raw.get("key")
        if not key or not isinstance(key, str):
            errors.append(f"{ctx}: missing key")
            continue
        if key in keys:
            errors.append(f"{ctx}: duplicate key {key!r}")
            continue
        keys.add(key)
        ctx = f"events[{idx}] ({key})"

        issuer_key = raw.get("issuer")
        if not issuer_key or not isinstance(issuer_key, str):
            errors.append(f"{ctx}: missing issuer")
            continue
        if resolve_issuer(session, issuer_key) is None:
            errors.append(f"{ctx}: issuer {issuer_key!r} not found in DB (Issuer.key)")
            continue

        try:
            event_date = _parse_date(raw.get("event_date"), field="event_date", ctx=ctx)
            available_from = _parse_date(raw.get("available_from"), field="available_from", ctx=ctx)
        except ReplayValidationError as exc:
            errors.append(str(exc))
            continue

        if prev_available is not None and available_from < prev_available:
            errors.append(
                f"{ctx}: available_from {available_from} is before previous "
                f"{prev_available} (events must be in available_from order)"
            )
        prev_available = available_from

        if available_from < window.start or available_from > window.end:
            errors.append(
                f"{ctx}: available_from {available_from} outside window "
                f"[{window.start}, {window.end}]"
            )

        sources_raw = raw.get("sources")
        if not isinstance(sources_raw, list) or not sources_raw:
            errors.append(f"{ctx}: sources must be a non-empty list")
            continue
        sources: list[dict[str, Any]] = []
        source_ok = True
        for sidx, src in enumerate(sources_raw):
            sctx = f"{ctx}.sources[{sidx}]"
            if not isinstance(src, dict):
                errors.append(f"{sctx}: must be an object")
                source_ok = False
                continue
            try:
                url = _require_https(src.get("url"), ctx=sctx)
            except ReplayValidationError as exc:
                errors.append(str(exc))
                source_ok = False
                continue
            filed_raw = src.get("filed")
            if filed_raw is not None:
                try:
                    _parse_date(filed_raw, field="filed", ctx=sctx)
                except ReplayValidationError as exc:
                    errors.append(str(exc))
                    source_ok = False
                    continue
            sources.append(
                {
                    "form": src.get("form"),
                    "filed": filed_raw,
                    "url": url,
                    "description": src.get("description"),
                }
            )
        if not source_ok or not sources:
            continue

        try:
            eligible, confirming = split_sources_by_availability(sources, available_from)
        except ReplayValidationError as exc:
            errors.append(str(exc))
            continue
        if not eligible:
            errors.append(
                f"{ctx}: no sources with filed ≤ available_from {available_from.isoformat()}"
            )
            continue

        title = raw.get("title")
        event_type = raw.get("event_type")
        if not title or not isinstance(title, str):
            errors.append(f"{ctx}: missing title")
            continue
        if not event_type or not isinstance(event_type, str):
            errors.append(f"{ctx}: missing event_type")
            continue

        notes = raw.get("notes")
        if notes is not None and not isinstance(notes, str):
            notes = None

        parsed.append(
            {
                "key": key,
                "issuer_key": issuer_key,
                "event_type": event_type,
                "event_date": event_date,
                "available_from": available_from,
                "title": title,
                "sources": eligible,
                "confirming_sources": confirming,
                "expected_changes": list(raw.get("expected_changes") or []),
                "expected_alert": bool(raw.get("expected_alert")),
                "why": raw.get("why") if isinstance(raw.get("why"), (str, type(None))) else None,
                "notes": notes,
            }
        )

    if errors:
        raise ReplayValidationError("; ".join(errors))

    upserted: list[ReplayExpectation] = []
    for item in parsed:
        row = session.scalars(
            select(ReplayExpectation).where(ReplayExpectation.key == item["key"])
        ).first()
        if row is None:
            row = ReplayExpectation(key=item["key"])
            session.add(row)
        row.issuer_key = item["issuer_key"]
        row.event_type = item["event_type"]
        row.event_date = item["event_date"]
        row.available_from = item["available_from"]
        row.title = item["title"]
        row.sources = item["sources"]
        row.confirming_sources = item["confirming_sources"]
        row.expected_changes = item["expected_changes"]
        row.expected_alert = item["expected_alert"]
        row.why = item["why"]
        row.notes = item["notes"]
        upserted.append(row)

    session.flush()
    return upserted


def _existing_event(session: Session, replay_key: str) -> Event | None:
    return session.scalars(select(Event).where(Event.replay_key == replay_key)).first()


def _store_filing(
    session: Session,
    *,
    issuer: Issuer | None,
    url: str,
    form_type: str,
    filed_at: date | None,
    title: str | None,
    text: str,
) -> Filing:
    # Postgres text rejects NUL; strip defensively (PDF/binary mishaps).
    clean = (text or "").replace("\x00", "")
    content_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    row = session.scalars(select(Filing).where(Filing.url == url)).first()
    if row is None:
        row = Filing(url=url)
        session.add(row)
    if issuer is not None:
        row.issuer_id = issuer.id
    row.form_type = form_type
    row.filed_at = filed_at
    row.title = (title or "")[:512] or None
    row.content_hash = content_hash
    row.body_text = clean
    session.flush()
    return row


def _fetch_source_text(clients: ReplayClients, url: str) -> str:
    if _is_sec_url(url):
        return clients.edgar.fetch_text(url)
    return clients.newsroom.fetch_text(url)


_EDGAR_INDEX_RE = re.compile(
    r"/Archives/edgar/data/(\d+)/(\d+)/(\d{10}-\d{2}-\d{6})-index\.htm?$",
    re.I,
)

_SKIP_DOC_SUFFIXES = (
    ".xml",
    ".xsd",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".pdf",
    ".zip",
    ".txt",
)
_SKIP_DOC_TYPES = frozenset(
    {
        "graphic",
        "xml",
        "zip",
        "complete submission text file",
    }
)


def parse_edgar_index_url(url: str) -> tuple[str, str] | None:
    """Return ``(cik, accession)`` when ``url`` is an EDGAR ``*-index.htm`` page."""
    path = urlparse(url).path
    match = _EDGAR_INDEX_RE.search(path)
    if match is None:
        return None
    return match.group(1), match.group(3)


def _is_edgar_index_url(url: str) -> bool:
    return parse_edgar_index_url(url) is not None


def _skip_index_document(name: str, doc_type: str) -> bool:
    """Drop XBRL, images, graphics, R*.htm viewer files, and complete submissions."""
    lower_name = (name or "").lower()
    lower_type = (doc_type or "").strip().lower()
    if lower_type in _SKIP_DOC_TYPES or lower_type.startswith("ex-101"):
        return True
    if any(lower_name.endswith(suf) for suf in _SKIP_DOC_SUFFIXES):
        return True
    # iXBRL inline viewer fragments: R1.htm, R10.htm, …
    return bool(re.fullmatch(r"r\d+\.htm", lower_name))


def select_index_documents(docs: list[Any]) -> list[Any]:
    """Primary filing document first, then EX-99.x press-release exhibits."""
    kept = [
        d for d in docs if not _skip_index_document(getattr(d, "name", ""), getattr(d, "type", ""))
    ]
    if not kept:
        return []
    primary = None
    exhibits: list[Any] = []
    for doc in kept:
        dtype = str(getattr(doc, "type", "") or "").strip().upper()
        if dtype.startswith("EX-99"):
            exhibits.append(doc)
        elif primary is None:
            primary = doc
    ordered: list[Any] = []
    if primary is not None:
        ordered.append(primary)
    ordered.extend(exhibits)
    # If the index only had EX-99s (unusual), still return them.
    return ordered or exhibits


def _filings_from_edgar_index(
    session: Session,
    clients: ReplayClients,
    *,
    issuer: Issuer | None,
    index_url: str,
    form_hint: str,
    filed_at: date | None,
) -> list[Filing]:
    """Resolve an EDGAR index URL into primary + EX-99 Filing rows with body_text."""
    parsed = parse_edgar_index_url(index_url)
    if parsed is None:
        raise ValueError(f"not an EDGAR index URL: {index_url}")
    cik, accession = parsed
    filing_documents = getattr(clients.edgar, "filing_documents", None)
    if not callable(filing_documents):
        raise AttributeError("edgar client has no filing_documents")
    docs = select_index_documents(list(filing_documents(cik, accession)))
    if not docs:
        raise ValueError(f"no usable documents in EDGAR index {index_url}")

    out: list[Filing] = []
    for i, doc in enumerate(docs):
        raw = clients.edgar.fetch_text(doc.url)
        # Prefer the document's own <title>; fall back to type / filename.
        doc_title = extract_document_title(raw) or (doc.type or doc.name or None)
        dtype = str(doc.type or "").strip()
        if i == 0:
            form_type = _filing_form_type(_normalize_form(form_hint or dtype))
        elif dtype.upper().startswith("EX-99"):
            form_type = "press_release"
        else:
            form_type = _filing_form_type(_normalize_form(dtype or form_hint))
        out.append(
            _store_filing(
                session,
                issuer=issuer,
                url=doc.url,
                form_type=form_type,
                filed_at=filed_at,
                title=doc_title,
                text=raw,
            )
        )
    return out


def _filings_for_source(
    session: Session,
    clients: ReplayClients,
    *,
    issuer: Issuer | None,
    src: dict[str, Any],
    available_from: date,
) -> list[Filing]:
    """Fetch one replay source into one or more Filing rows (index → docs)."""
    url = str(src["url"])
    filed = src.get("filed")
    filed_at = date.fromisoformat(str(filed)) if filed else available_from
    form_hint = _normalize_form(src.get("form"))

    if (
        _is_edgar_index_url(url)
        and _is_sec_url(url)
        and callable(getattr(clients.edgar, "filing_documents", None))
    ):
        try:
            return _filings_from_edgar_index(
                session,
                clients,
                issuer=issuer,
                index_url=url,
                form_hint=form_hint,
                filed_at=filed_at,
            )
        except Exception:
            logger.exception("edgar_index_resolve_failed", extra={"url": url})
            # Fall through to single-URL fetch (tests that map index URL → body).

    text = _fetch_source_text(clients, url)
    doc_title = extract_document_title(text)
    return [
        _store_filing(
            session,
            issuer=issuer,
            url=url,
            form_type=_filing_form_type(form_hint),
            filed_at=filed_at,
            title=doc_title,
            text=text,
        )
    ]


def _link_event_filing(session: Session, event: Event, filing: Filing) -> None:
    existing = session.scalars(
        select(EventFiling).where(
            EventFiling.event_id == event.id, EventFiling.filing_id == filing.id
        )
    ).first()
    if existing is None:
        session.add(EventFiling(event_id=event.id, filing_id=filing.id))


def _source_identity(src: dict[str, Any] | None) -> str | None:
    """Stable identity for a primary source (accession preferred, else URL)."""
    if not src:
        return None
    acc = src.get("accession")
    if acc:
        return str(acc).strip()
    url = str(src.get("url") or "").strip()
    if not url:
        return None
    match = re.search(r"(\d{10}-\d{2}-\d{6})", url)
    if match:
        return match.group(1)
    return url


def _filing_group_key(expectation: ReplayExpectation) -> tuple[str, str] | None:
    primary = (expectation.sources or [None])[0]
    ident = _source_identity(primary if isinstance(primary, dict) else None)
    if not ident:
        return None
    return (ident, expectation.available_from.isoformat())


def _top_parent_issuer(session: Session, issuers: list[Issuer]) -> Issuer:
    """Among a set of issuers, pick the top parent in the group (holdco if present)."""
    if len(issuers) == 1:
        return issuers[0]
    ids = {i.id for i in issuers}
    roots = [i for i in issuers if i.parent_id is None or i.parent_id not in ids]
    if not roots:
        roots = list(issuers)
    for r in roots:
        if r.parent_id is None:
            return r
    # Prefer the issuer whose key looks like a corp/holdco.
    for r in roots:
        key = (r.key or "").lower()
        if key.endswith("_corp") or "parent" in key or key.endswith("_inc"):
            return r
    return sorted(roots, key=lambda i: i.key or i.name)[0]


def _merged_replay_key(identity: str, available_from: date) -> str:
    return f"filing:{identity}:{available_from.isoformat()}"


def _create_seeded_event_group(
    session: Session,
    expectations: list[ReplayExpectation],
    clients: ReplayClients,
) -> Event | None:
    """Create one Event for a group of expectations that share a source filing + date.

    Single-expectation groups keep ``replay_key == expectation.key``. Multi-expectation
    groups (combined 10-Q etc.) use the top parent issuer, a ``filing:…`` replay_key,
    list every registrant in the summary, and link all expectations to the event.
    """
    if not expectations:
        return None
    expectations = sorted(expectations, key=lambda e: e.key)

    # Already linked → nothing to create.
    if all(exp.event_id for exp in expectations):
        return None
    # Partial links: attach remaining to the existing event.
    linked_ids = {exp.event_id for exp in expectations if exp.event_id}
    if linked_ids:
        event_id = next(iter(linked_ids))
        event = session.get(Event, event_id)
        if event is not None:
            for exp in expectations:
                exp.event_id = event.id
            session.flush()
        return None

    # Idempotent: merged key or any member key already has an event.
    group_key = _filing_group_key(expectations[0])
    if group_key and len(expectations) > 1:
        merged_key = _merged_replay_key(group_key[0], expectations[0].available_from)
        existing = _existing_event(session, merged_key)
        if existing is not None:
            for exp in expectations:
                exp.event_id = existing.id
            session.flush()
            return None
    for exp in expectations:
        existing = _existing_event(session, exp.key)
        if existing is not None:
            for e in expectations:
                e.event_id = existing.id
            session.flush()
            return None

    issuers: list[Issuer] = []
    for exp in expectations:
        iss = resolve_issuer(session, exp.issuer_key)
        if iss is not None and all(i.id != iss.id for i in issuers):
            issuers.append(iss)
    primary_exp = expectations[0]
    if not issuers:
        parent = None
    elif len(expectations) == 1:
        parent = issuers[0]
    else:
        parent = _top_parent_issuer(session, issuers)

    primary_src = (primary_exp.sources or [None])[0]
    if primary_src is None or not isinstance(primary_src, dict):
        return None

    filings: list[Filing] = []
    # Fetch sources once (from the parent issuer context); link all docs to the event.
    for src in primary_exp.sources or []:
        if not isinstance(src, dict):
            continue
        filings.extend(
            _filings_for_source(
                session,
                clients,
                issuer=parent,
                src=src,
                available_from=primary_exp.available_from,
            )
        )

    primary_form = _normalize_form(primary_src.get("form"))
    primary_filed_s = primary_src.get("filed")
    primary_filed = (
        date.fromisoformat(str(primary_filed_s)) if primary_filed_s else primary_exp.available_from
    )
    registrant_names = [i.name for i in issuers] if issuers else [primary_exp.issuer_key]
    if len(expectations) > 1:
        issuer_name = parent.name if parent else primary_exp.issuer_key
        summary = (
            f"{neutral_summary(issuer_name=issuer_name, form=primary_form, filed=primary_filed)}; "
            f"registrants: {', '.join(registrant_names)}"
        )
        replay_key = _merged_replay_key(
            _source_identity(primary_src) or primary_exp.key,
            primary_exp.available_from,
        )
        # Prefer a substantive event_type from the group (not just periodic_filing).
        event_type = _agent_event_type(primary_form)
        for exp in expectations:
            et = (exp.event_type or "").strip()
            if et and et not in {"periodic_filing", "10-Q", "10-K"}:
                event_type = et
                break
    else:
        issuer_name = parent.name if parent else primary_exp.issuer_key
        summary = neutral_summary(issuer_name=issuer_name, form=primary_form, filed=primary_filed)
        replay_key = primary_exp.key
        event_type = _agent_event_type(primary_form)

    event = Event(
        replay_key=replay_key,
        event_type=event_type,
        issuer_id=parent.id if parent else None,
        event_date=primary_exp.available_from,
        source_filing_id=filings[0].id if filings else None,
        summary=summary,
        status="pending",
    )
    session.add(event)
    session.flush()
    for filing in filings:
        _link_event_filing(session, event, filing)
    for exp in expectations:
        exp.event_id = event.id
    session.flush()
    return event


def _create_seeded_event(
    session: Session,
    expectation: ReplayExpectation,
    clients: ReplayClients,
) -> Event | None:
    """Back-compat single-expectation create (used by tests). Prefer group create."""
    return _create_seeded_event_group(session, [expectation], clients)


def _create_maturity_events(session: Session, until: date, window_start: date) -> list[Event]:
    created: list[Event] = []
    instruments = session.scalars(
        select(Instrument).where(
            Instrument.status == "active",
            Instrument.maturity >= window_start,
            Instrument.maturity <= until,
        )
    ).all()
    for inst in instruments:
        key = f"maturity:{inst.key or inst.id}:{inst.maturity.isoformat()}"
        if _existing_event(session, key) is not None:
            continue
        issuer = session.get(Issuer, inst.issuer_id)
        issuer_name = issuer.name if issuer else "Issuer"
        event = Event(
            replay_key=key,
            event_type="maturity",
            issuer_id=inst.issuer_id,
            event_date=inst.maturity,
            summary=maturity_summary(issuer_name=issuer_name, instrument=inst),
            status="pending",
        )
        session.add(event)
        created.append(event)
    session.flush()
    return created


def _create_rate_update_events(session: Session, until: date, window_start: date) -> list[Event]:
    created: list[Event] = []
    for friday in _fridays_on_or_before(window_start, until):
        key = f"rate_update:{friday.isoformat()}"
        if _existing_event(session, key) is not None:
            continue
        # Built from RateObservation only — no FRED API calls during replay.
        session.scalars(
            select(RateObservation).where(RateObservation.obs_date <= friday).limit(1)
        ).first()
        event = Event(
            replay_key=key,
            event_type="rate_update",
            issuer_id=None,
            event_date=friday,
            summary=f"Rate update, {_format_day(friday)}",
            status="pending",
        )
        session.add(event)
        created.append(event)
    session.flush()
    return created


def _replay_cursor(session: Session, window_start: date) -> date:
    """Last day the replay has already covered (day before window if empty)."""
    latest = session.scalar(select(func.max(Event.event_date)))
    if latest is None:
        return window_start - timedelta(days=1)
    return latest


def advance_day(
    session: Session,
    d: date,
    sources: ReplayClients,
    *,
    window_start: date | None = None,
) -> list[Event]:
    """Create pending ``Event`` rows newly available on exactly ``d``."""
    start = window_start or date(2026, 1, 1)
    if d < start:
        return []
    created: list[Event] = []

    expectations = session.scalars(
        select(ReplayExpectation)
        .where(ReplayExpectation.available_from == d)
        .order_by(ReplayExpectation.key)
    ).all()
    groups: dict[tuple[str, str] | None, list[ReplayExpectation]] = {}
    for exp in expectations:
        groups.setdefault(_filing_group_key(exp), []).append(exp)
    # Ungrouped (no source identity) stay one-per-expectation under a unique None bucket
    # — split them so they don't falsely merge.
    for key, group in list(groups.items()):
        if key is None:
            for exp in group:
                event = _create_seeded_event_group(session, [exp], sources)
                if event is not None:
                    created.append(event)
            continue
        event = _create_seeded_event_group(session, group, sources)
        if event is not None:
            created.append(event)

    created.extend(_create_maturity_events(session, d, d))
    created.extend(_create_rate_update_events(session, d, d))
    return created


def step_to_next(
    session: Session,
    until: date,
    sources: ReplayClients,
    *,
    window_start: date | None = None,
    after: date | None = None,
) -> list[Event]:
    """Advance one calendar day at a time; stop at the first day that yields events.

    Use this from ``replay_tick`` and CLI ``--step`` so intervening agent runs can update
    instrument status before later maturity events are created.
    """
    start = window_start or date(2026, 1, 1)
    cursor = after if after is not None else _replay_cursor(session, start)
    d = cursor + timedelta(days=1)
    while d <= until:
        created = advance_day(session, d, sources, window_start=start)
        if created:
            return created
        d += timedelta(days=1)
    return []


def advance_to(
    session: Session,
    d: date,
    sources: ReplayClients,
    *,
    window_start: date | None = None,
) -> list[Event]:
    """Create pending ``Event`` rows for everything newly available on or before ``d``.

    Idempotent: existing ``replay_key`` values are left unchanged.

    A jump of more than one day creates maturity events from whatever
    ``Instrument.status`` is still ``active``. Prefer ``step_to_next`` / CLI ``--step``
    during live replay so the agent can mark redeemed or matured bonds before later
    calendar days are opened; a multi-day ``--until`` jump skips that.
    """
    start = window_start or date(2026, 1, 1)
    created: list[Event] = []

    expectations = session.scalars(
        select(ReplayExpectation)
        .where(ReplayExpectation.available_from <= d)
        .order_by(ReplayExpectation.available_from, ReplayExpectation.key)
    ).all()
    groups: dict[tuple[str, str] | None, list[ReplayExpectation]] = {}
    for exp in expectations:
        groups.setdefault(_filing_group_key(exp), []).append(exp)
    for key, group in groups.items():
        if key is None:
            for exp in group:
                event = _create_seeded_event_group(session, [exp], sources)
                if event is not None:
                    created.append(event)
            continue
        event = _create_seeded_event_group(session, group, sources)
        if event is not None:
            created.append(event)

    created.extend(_create_maturity_events(session, d, start))
    created.extend(_create_rate_update_events(session, d, start))
    return created


def agent_visible_payload(event: Event) -> dict[str, Any]:
    """Fields an agent may see for an event (excludes evaluation / replay keys)."""
    payload = {
        "id": event.id,
        "event_type": event.event_type,
        "issuer_id": event.issuer_id,
        "event_date": event.event_date.isoformat() if event.event_date else None,
        "source_filing_id": event.source_filing_id,
        "summary": event.summary,
        "status": event.status,
    }
    for forbidden in AGENT_FORBIDDEN_FIELDS:
        assert forbidden not in payload
    return payload


def default_clients() -> ReplayClients:
    """Live clients for CLI / worker (require SEC_USER_AGENT; newsroom same)."""
    from app.config import get_settings
    from app.sources.edgar import EdgarClient
    from app.sources.newsroom import NewsroomClient

    settings = get_settings()
    return ReplayClients(
        edgar=EdgarClient(settings=settings),
        newsroom=NewsroomClient(settings=settings),
    )


def run_until(
    until: date,
    *,
    path: Path | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    clients: ReplayClients | None = None,
    step: bool = False,
) -> list[Event]:
    """Load expectations and advance replay through ``until``.

    With ``step=True``, walks one calendar day at a time and stops at the first day
    that creates events. Without ``step``, jumps straight to ``until`` — a jump of
    more than a day creates maturity events from stale instrument status.
    """
    owns_clients = clients is None
    clients = clients or default_clients()
    created: list[Event] = []
    try:
        with session_factory() as session:
            load_replay_events(session, path)
            if step:
                created = step_to_next(session, until, clients)
            else:
                created = advance_to(session, until, clients)
            session.commit()
    finally:
        if owns_clients:
            for client in (clients.edgar, clients.newsroom):
                close = getattr(client, "close", None)
                if callable(close):
                    close()
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Advance Kernel replay to a date. Prefer --step so the agent can update "
            "instrument status between eventful days; a multi-day --until jump creates "
            "maturity events from stale instrument status."
        )
    )
    parser.add_argument(
        "--until",
        required=True,
        type=date.fromisoformat,
        help="Inclusive as-of date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--step",
        action="store_true",
        help=(
            "Advance one calendar day at a time and stop at the first day with pending "
            "events. A jump of more than a day (plain --until) creates maturity events "
            "while instrument status may still be stale."
        ),
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Path to replay_events JSON (default: backend/seed/replay_events_2026.json)",
    )
    args = parser.parse_args(argv)
    created = run_until(args.until, path=args.path, step=args.step)
    mode = "step" if args.step else "jump"
    landed = max((e.event_date for e in created), default=None)
    landed_s = landed.isoformat() if landed is not None else "none"
    print(
        f"replay {mode} through {args.until.isoformat()} (landed {landed_s}): "
        f"{len(created)} new event(s) at {datetime.now().isoformat(timespec='seconds')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
