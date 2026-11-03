"""Poll EDGAR and newsrooms for new live documents and create Events.

Dedupe: accession number (EDGAR) or URL (press releases); one Event per shared
filing identity, marked ``source='live'``. Never creates ReplayExpectation rows.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventFiling, Filing, Issuer, LiveConfig, Watchlist
from app.sources.edgar import EdgarClient
from app.sources.newsroom import NewsroomClient, ReleaseRef

# First live backfill: any filing since this date that the replay window missed.
DEFAULT_LIVE_SINCE = date(2026, 10, 27)

LIVE_EDGAR_FORMS = (
    "10-Q",
    "10-K",
    "8-K",
    "10-Q/A",
    "10-K/A",
    "8-K/A",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
)

_424B_RE = re.compile(r"^424B", re.I)

# Banks file pricing supplements for structured notes they sell to customers under
# 424B2/424B3 many times a day. They don't change the bank's own debt profile, and
# reading each one would spend the live budget, so live mode skips them for banks.
_BANK_NOTE_FORMS = frozenset({"424B2", "424B3"})


def is_bank_structured_note(sector: str | None, form: str | None) -> bool:
    """True for a bank's structured-note pricing supplement (not monitored live)."""
    from app.metrics.peers import is_bank_sector

    return is_bank_sector(sector) and (form or "").upper().replace(" ", "") in _BANK_NOTE_FORMS


@dataclass(frozen=True)
class LiveDocument:
    issuer_key: str
    form: str
    filed_at: date
    identity: str  # accession or url
    filing_id: str
    event_id: str
    created: bool


def _git_sha() -> str | None:
    import os
    import subprocess

    for key in ("GIT_SHA", "COMMIT_SHA", "GITHUB_SHA"):
        env = (os.environ.get(key) or "").strip()
        if env:
            return env[:40]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else None


def ensure_live_config(session: Session, *, prompt_version: str) -> LiveConfig:
    """Return the freeze row, creating it on first live run."""
    row = session.scalars(select(LiveConfig).limit(1)).first()
    if row is not None:
        return row
    row = LiveConfig(
        go_live_at=datetime.now(UTC),
        git_sha=_git_sha(),
        prompt_version=prompt_version,
    )
    session.add(row)
    session.flush()
    return row


def _watchlist_issuers(session: Session) -> list[Issuer]:
    roots = session.scalars(
        select(Issuer)
        .join(Watchlist, Watchlist.issuer_id == Issuer.id)
        .where(Issuer.parent_id.is_(None))
        .order_by(Issuer.key)
    ).all()
    return list(roots)


def _monitor_from(session: Session, issuer: Issuer) -> date | None:
    row = session.scalars(select(Watchlist).where(Watchlist.issuer_id == issuer.id)).first()
    return row.monitor_from if row is not None else None


def _replay_key_for_identity(identity: str) -> str:
    if re.fullmatch(r"\d{10}-\d{2}-\d{6}", identity):
        return f"live:edgar:{identity}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"live:url:{digest}"


def _existing_live_event(session: Session, identity: str) -> Event | None:
    return session.scalars(
        select(Event).where(Event.replay_key == _replay_key_for_identity(identity))
    ).first()


def _event_type_for_form(form: str) -> str:
    ft = (form or "").upper().replace(" ", "")
    if ft.startswith("10Q"):
        return "10-Q"
    if ft.startswith("10K"):
        return "10-K"
    if ft.startswith("8K"):
        return "8-K"
    if _424B_RE.match(ft):
        return form
    if ft in {"PRESS_RELEASE", "PRESS-RELEASE", "PRESSRELEASE"}:
        return "press_release"
    return form or "filing"


def _link_filing(session: Session, event: Event, filing: Filing) -> None:
    exists = session.scalars(
        select(EventFiling).where(
            EventFiling.event_id == event.id, EventFiling.filing_id == filing.id
        )
    ).first()
    if exists is None:
        session.add(EventFiling(event_id=event.id, filing_id=filing.id))


def _create_live_event(
    session: Session,
    *,
    issuer: Issuer,
    filing: Filing,
    identity: str,
    form: str,
    filed_at: date,
) -> tuple[Event, bool]:
    existing = _existing_live_event(session, identity)
    if existing is not None:
        _link_filing(session, existing, filing)
        if existing.source_filing_id is None:
            existing.source_filing_id = filing.id
        return existing, False
    # Also skip if a replay event already covers this accession/URL.
    if filing.accession_number:
        prior = session.scalars(
            select(Event)
            .join(EventFiling, EventFiling.event_id == Event.id)
            .join(Filing, Filing.id == EventFiling.filing_id)
            .where(Filing.accession_number == filing.accession_number)
        ).first()
        if prior is not None:
            return prior, False
    if filing.url:
        prior_url = session.scalars(
            select(Event).where(Event.source_filing_id == filing.id)
        ).first()
        if prior_url is not None:
            return prior_url, False

    event = Event(
        replay_key=_replay_key_for_identity(identity),
        event_type=_event_type_for_form(form),
        issuer_id=issuer.id,
        event_date=filed_at,
        source_filing_id=filing.id,
        summary=f"{issuer.name}: {form} filed {filed_at.isoformat()}",
        status="pending",
        source="live",
    )
    session.add(event)
    session.flush()
    _link_filing(session, event, filing)
    return event, True


def poll_edgar_for_issuer(
    session: Session,
    edgar: EdgarClient,
    issuer: Issuer,
    *,
    since: date,
    until: date,
) -> list[LiveDocument]:
    if not issuer.cik or not issuer.key:
        return []
    refs = edgar.list_filings(issuer.cik, LIVE_EDGAR_FORMS, since, until)
    # Also keep any 424B* the submissions API returns under a slight name variant.
    extra = [
        r
        for r in edgar.list_filings(issuer.cik, ("424B2", "424B5"), since, until)
        if r.accession not in {x.accession for x in refs}
    ]
    refs = list(refs) + extra
    refs = [r for r in refs if not is_bank_structured_note(issuer.sector, getattr(r, "form", None))]
    out: list[LiveDocument] = []
    for ref in refs:
        rows = edgar.upsert_filings(session, issuer, [ref])
        filing = rows[0]
        try:
            if not filing.body_text:
                edgar.fetch_filing_text(session, filing)
        except Exception as exc:
            print(f"live: text fetch failed {issuer.key} {ref.accession}: {exc}")
        event, created = _create_live_event(
            session,
            issuer=issuer,
            filing=filing,
            identity=ref.accession,
            form=ref.form,
            filed_at=ref.filing_date,
        )
        out.append(
            LiveDocument(
                issuer_key=issuer.key,
                form=ref.form,
                filed_at=ref.filing_date,
                identity=ref.accession,
                filing_id=filing.id,
                event_id=event.id,
                created=created,
            )
        )
    return out


def poll_newsroom_for_issuer(
    session: Session,
    newsroom: NewsroomClient,
    issuer: Issuer,
    *,
    since: date,
    until: date,
) -> list[LiveDocument]:
    if not issuer.key:
        return []
    try:
        releases: list[ReleaseRef] = newsroom.list_releases(issuer.key, since, until)
    except Exception as exc:
        print(f"live: newsroom list failed {issuer.key}: {exc}")
        return []
    out: list[LiveDocument] = []
    for rel in releases:
        rows = newsroom.upsert_releases(session, issuer, [rel])
        filing = rows[0]
        try:
            if not filing.body_text:
                newsroom.fetch_release_text(session, filing)
        except Exception as exc:
            print(f"live: newsroom text failed {issuer.key} {rel.url}: {exc}")
        event, created = _create_live_event(
            session,
            issuer=issuer,
            filing=filing,
            identity=rel.url,
            form="press_release",
            filed_at=rel.published,
        )
        out.append(
            LiveDocument(
                issuer_key=issuer.key,
                form="press_release",
                filed_at=event.event_date,
                identity=rel.url,
                filing_id=filing.id,
                event_id=event.id,
                created=created,
            )
        )
    return out


def poll_all(
    session: Session,
    edgar: EdgarClient,
    newsroom: NewsroomClient,
    *,
    since: date,
    until: date | None = None,
) -> list[LiveDocument]:
    """Poll every watchlist root issuer; return documents seen (new + known).

    Ticker-onboarded issuers set ``Watchlist.monitor_from`` so their first live
    poll only sees filings on/after onboard day (no agent backfill of history).
    """
    until = until or date.today()
    docs: list[LiveDocument] = []
    for issuer in _watchlist_issuers(session):
        floor = _monitor_from(session, issuer)
        issuer_since = max(since, floor) if floor is not None else since
        docs.extend(poll_edgar_for_issuer(session, edgar, issuer, since=issuer_since, until=until))
        docs.extend(
            poll_newsroom_for_issuer(session, newsroom, issuer, since=issuer_since, until=until)
        )
    session.flush()
    return docs


# Live schedule: at most one resume of a paused run, then leave it paused.
MAX_LIVE_RESUMES = 1
# Runs that must never be re-queued by the live job (needs-an-analyst included).
_LIVE_TERMINAL_RUN = frozenset({"alerted", "logged", "needs_input"})


def live_resume_attempts(run: Any) -> int:
    """How many times this paused run has already been resumed.

    Prefers an explicit ``live_resume_attempts`` counter written by the live job;
    otherwise derives from agent ``sessions`` (first session = initial run).
    """
    meta = run.result if isinstance(getattr(run, "result", None), dict) else {}
    explicit = meta.get("live_resume_attempts")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit
    sessions = meta.get("sessions") or []
    if isinstance(sessions, list) and sessions:
        return max(0, len(sessions) - 1)
    return 0


def pending_live_events(session: Session) -> list[Event]:
    """Live events the schedule should still run.

    Includes pending/failed with no terminal run, and paused runs that have not
    yet used their single resume attempt. Never re-queues ``needs_input`` /
    alerted / logged, and never resumes a paused run a second time.
    """
    from app.models.trace import AgentRun

    events = list(
        session.scalars(
            select(Event)
            .where(Event.source == "live")
            .order_by(Event.event_date.asc(), Event.detected_at.asc())
        ).all()
    )
    out: list[Event] = []
    sectors: dict[str, str | None] = {}
    for event in events:
        if event.issuer_id not in sectors:
            iss = session.get(Issuer, event.issuer_id) if event.issuer_id else None
            sectors[event.issuer_id] = iss.sector if iss else None
        if is_bank_structured_note(sectors[event.issuer_id], event.event_type):
            continue
        run = session.get(AgentRun, event.run_id) if event.run_id else None
        if run is not None and run.status in _LIVE_TERMINAL_RUN:
            continue
        if run is not None and run.status == "paused":
            if live_resume_attempts(run) >= MAX_LIVE_RESUMES:
                continue
            out.append(event)
            continue
        if event.status in {"pending", "failed"}:
            out.append(event)
    return out


def mark_live_resume_attempt(run: Any) -> int:
    """Record that the live job is about to resume ``run``. Returns the new count."""
    meta = dict(run.result or {}) if isinstance(getattr(run, "result", None), dict) else {}
    nxt = live_resume_attempts(run) + 1
    meta["live_resume_attempts"] = nxt
    run.result = meta
    return nxt


def live_since_date(session: Session) -> date | None:
    row = session.scalars(select(LiveConfig).limit(1)).first()
    if row is None:
        return None
    return row.go_live_at.date()


# Live UI vocabulary (never surface raw run labels like needs_input).
_OUTCOME_RAISED = "raised alert"
_OUTCOME_REVIEWED = "reviewed, no alert"
_OUTCOME_ANALYST = "needs an analyst"
_OUTCOME_PAUSED = "paused"
_OUTCOME_FAILED = "failed"
_OUTCOME_PENDING = "pending"

_FORM_WORDS: dict[str, str] = {
    "10-Q": "quarterly report",
    "10-Q/A": "quarterly report (amendment)",
    "10-K": "annual report",
    "10-K/A": "annual report (amendment)",
    "8-K": "current report",
    "8-K/A": "current report (amendment)",
    "424B2": "prospectus supplement (bond offering)",
    "424B3": "prospectus supplement (bond offering)",
    "424B4": "prospectus supplement (bond offering)",
    "424B5": "prospectus supplement (bond offering)",
    "424B7": "prospectus supplement (bond offering)",
    "press_release": "press release",
    "debt_details": "debt details",
}


def form_words(form: str | None) -> str:
    """Human label for a filing form code."""
    if not form:
        return "document"
    key = form.strip()
    if key in _FORM_WORDS:
        return _FORM_WORDS[key]
    upper = key.upper().replace(" ", "")
    if upper.startswith("424B"):
        return "prospectus supplement (bond offering)"
    return _FORM_WORDS.get(upper, key.replace("_", " ").lower())


def _normalize_live_outcome(status: str) -> str:
    s = (status or "").strip().lower()
    if s in {"alerted", "raised alert"}:
        return _OUTCOME_RAISED
    if s in {"logged", "reviewed, no alert", "processed"}:
        return _OUTCOME_REVIEWED
    if s in {"needs_input", "refused", "needs an analyst", "needs input"}:
        return _OUTCOME_ANALYST
    if s == "paused":
        return _OUTCOME_PAUSED
    if s == "failed":
        return _OUTCOME_FAILED
    if s in {"pending", "processing"}:
        return _OUTCOME_PENDING
    return s or _OUTCOME_PENDING


def _analyst_line(session: Session, run: Any | None, status: str) -> str | None:
    """Plain-English analyst line for the live feed (technical detail stays on the trace)."""
    if run is None:
        return None
    raw = (getattr(run, "error", None) or "").strip()
    from app.replay.runner import _needs_input_detail

    detail = _needs_input_detail(session, run) or raw
    outcome = _normalize_live_outcome(status)
    if outcome == _OUTCOME_PAUSED:
        return "Kernel paused on this document after hitting a turn or cost limit."
    if outcome == _OUTCOME_FAILED:
        return "Kernel failed while reading this document."
    if outcome != _OUTCOME_ANALYST:
        return None
    lower = (detail or "").lower()
    if any(
        token in lower
        for token in ("claims", "unsupported", "ground", "checker", "verify", "summary")
    ):
        return (
            "Kernel could not confirm what this document changes: the independent "
            "check rejected its summary. Open the document."
        )
    if detail:
        # Keep the feed readable — one short sentence; full text lives on the run.
        clipped = detail.strip()
        if len(clipped) > 160:
            clipped = clipped[:157].rstrip() + "…"
        return f"Kernel needs an analyst: {clipped}"
    return (
        "Kernel could not confirm what this document changes: the independent "
        "check rejected its summary. Open the document."
    )


_MECHANICAL_SUMMARY = re.compile(
    r"^.+:\s*[\w./-]+\s+filed\s+\d{4}-\d{2}-\d{2}\s*$",
    re.I,
)


def _document_title(
    *,
    form: str | None,
    filing_title: str | None,
    event_summary: str | None,
    alert_title: str | None,
) -> str:
    """Form name in words, plus the agent's one-line summary when present."""
    words = form_words(form or filing_title)
    headed = words[0].upper() + words[1:] if words else "Document"
    # Prefer a real agent summary (alert title) over mechanical "X: 10-Q filed …".
    candidates = [event_summary, alert_title]
    summary = ""
    for cand in candidates:
        text = (cand or "").strip()
        if not text:
            continue
        if _MECHANICAL_SUMMARY.match(text):
            continue
        if text.upper().replace(" ", "") in {
            (form or "").upper().replace(" ", ""),
            words.upper().replace(" ", ""),
        }:
            continue
        summary = text
        break
    if not summary:
        return headed
    if summary.lower().startswith(words.lower()):
        return summary[0].upper() + summary[1:]
    return f"{headed}: {summary}"


def live_document_rows(session: Session) -> list[dict[str, Any]]:
    """One row per live event for the overview document list."""
    from app.models import Alert, Filing, Issuer
    from app.models.trace import AgentRun

    rows: list[dict[str, Any]] = []
    # Cap: overview Live section only needs a recent slice; unbounded N+1
    # (run/alert/issuer/filing per event) dominated overview latency.
    events = list(
        session.scalars(
            select(Event)
            .where(Event.source == "live")
            .order_by(Event.event_date.desc(), Event.detected_at.desc())
            .limit(400)
        ).all()
    )
    for event in events:
        if len(rows) >= 40:
            break
        run = session.get(AgentRun, event.run_id) if event.run_id else None
        status = (run.status if run else event.status) or "pending"
        if status in {"pending", "processing"} and event.issuer_id:
            iss = session.get(Issuer, event.issuer_id)
            if iss is not None and is_bank_structured_note(iss.sector, event.event_type):
                continue
        if status == "needs_input" or status == "refused":
            raw_outcome = "needs_input"
        elif status in {"alerted", "logged", "paused", "failed", "pending", "processing"}:
            raw_outcome = status
        elif session.scalars(select(Alert).where(Alert.event_id == event.id)).first():
            raw_outcome = "alerted"
        elif event.status == "processed":
            raw_outcome = "logged"
        else:
            raw_outcome = status
        outcome = _normalize_live_outcome(raw_outcome)
        issuer = session.get(Issuer, event.issuer_id) if event.issuer_id else None
        alert = session.scalars(select(Alert).where(Alert.event_id == event.id)).first()
        reason = _analyst_line(session, run, raw_outcome)
        doc_url: str | None = None
        filing_form: str | None = event.event_type
        filing_title: str | None = None
        if event.source_filing_id:
            filing = session.get(Filing, event.source_filing_id)
            if filing is not None:
                doc_url = filing.url
                filing_form = filing.form_type or filing_form
                filing_title = filing.title
        title = _document_title(
            form=filing_form,
            filing_title=filing_title,
            event_summary=event.summary,
            alert_title=alert.title if alert else None,
        )
        rows.append(
            {
                "event_id": event.id,
                "alert_id": alert.id if alert else None,
                "issuer_key": issuer.key if issuer else None,
                "issuer_name": issuer.name if issuer else None,
                "form": filing_form,
                "event_date": event.event_date.isoformat() if event.event_date else None,
                "title": title,
                "outcome": outcome,
                "reason": reason,
                "cost_usd": float(run.total_cost_usd or 0) if run else None,
                "document_url": doc_url,
            }
        )
    return rows


def _live_outcomes(session: Session) -> list[str]:
    """Outcome label for every live event, in one query (the overview counts these)."""
    from app.models import Alert
    from app.models.trace import AgentRun

    alerted_events = set(
        session.scalars(select(Alert.event_id).where(Alert.event_id.is_not(None))).all()
    )
    rows = session.execute(
        select(Event.id, Event.status, Event.event_type, AgentRun.status, Issuer.sector)
        .outerjoin(AgentRun, AgentRun.id == Event.run_id)
        .outerjoin(Issuer, Issuer.id == Event.issuer_id)
        .where(Event.source == "live")
    ).all()
    out: list[str] = []
    for event_id, event_status, event_type, run_status, sector in rows:
        status = (run_status or event_status or "pending").lower()
        if status in {"pending", "processing"} and is_bank_structured_note(sector, event_type):
            continue
        if status in {"needs_input", "refused"}:
            raw = "needs_input"
        elif status in {"alerted", "logged", "paused", "failed", "pending", "processing"}:
            raw = status
        elif event_id in alerted_events:
            raw = "alerted"
        elif (event_status or "") == "processed":
            raw = "logged"
        else:
            raw = status
        out.append(_normalize_live_outcome(raw))
    return out


def live_stats(session: Session) -> dict[str, Any]:
    """Counts for the overview Live section (never mixed into replay score).

    Counts cover every live event; ``documents`` is the newest slice for the list.
    "Documents read" excludes documents still waiting to be read.
    """
    documents = live_document_rows(session)
    outcomes = _live_outcomes(session)
    alerted = sum(1 for o in outcomes if o == _OUTCOME_RAISED)
    logged = sum(1 for o in outcomes if o == _OUTCOME_REVIEWED)
    refused = sum(1 for o in outcomes if o == _OUTCOME_ANALYST)
    paused = sum(1 for o in outcomes if o == _OUTCOME_PAUSED)
    failed = sum(1 for o in outcomes if o == _OUTCOME_FAILED)
    cfg = session.scalars(select(LiveConfig).limit(1)).first()
    return {
        "go_live_at": cfg.go_live_at.isoformat() if cfg else None,
        "git_sha": cfg.git_sha if cfg else None,
        "prompt_version": cfg.prompt_version if cfg else None,
        "documents_read": alerted + logged + refused + paused + failed,
        "alerted": alerted,
        "logged": logged,
        "refused": refused,
        "paused": paused,
        "failed": failed,
        "documents": documents,
    }
