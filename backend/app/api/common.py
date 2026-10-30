"""Shared helpers for the dashboard read API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.normalize import find_quote_offset
from app.agent.text import PAGE_CHARS, page_count, page_slice
from app.models import AgentRun, Fact, Filing
from app.models.trace import ModelCall

HIDDEN_RUN_STATUSES = frozenset({"superseded", "abandoned"})


def json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def run_is_hidden(run: AgentRun | None) -> bool:
    if run is None:
        return False
    return (run.status or "") in HIDDEN_RUN_STATUSES


def visible_run_ids(
    session: Session,
    *,
    include_superseded: bool,
) -> set[str] | None:
    """Return None when all runs are visible; else the set of non-hidden run ids."""
    if include_superseded:
        return None
    rows = session.scalars(
        select(AgentRun.id).where(AgentRun.status.not_in(sorted(HIDDEN_RUN_STATUSES)))
    ).all()
    return set(rows)


def bucket_deltas(
    before_wall: dict[str, Any] | None,
    after_wall: dict[str, Any] | None,
) -> list[dict[str, str]]:
    before_buckets = ((before_wall or {}).get("buckets") or {}) if before_wall else {}
    after_buckets = ((after_wall or {}).get("buckets") or {}) if after_wall else {}
    # Issuer metrics may nest under "issuer".
    if not before_buckets and isinstance(before_wall, dict) and "issuer" in before_wall:
        before_buckets = (before_wall.get("issuer") or {}).get("buckets") or {}
        after_buckets = (after_wall or {}).get("issuer", {}).get("buckets") or {}
    out: list[dict[str, str]] = []
    for label in sorted(set(before_buckets) | set(after_buckets)):
        b_raw = (before_buckets.get(label) or {}).get("total_outstanding", 0)
        a_raw = (after_buckets.get(label) or {}).get("total_outstanding", 0)
        b, a = str(b_raw or 0), str(a_raw or 0)
        if b != a:
            out.append({"label": label, "before": b, "after": a})
    return out


def fact_page_index(filing: Filing, fact: Fact) -> int | None:
    text = filing.body_text or ""
    if not text or not fact.exact_quote:
        return None
    offset = find_quote_offset(text, fact.exact_quote)
    if offset is None:
        # Try parsing "page N" from location.
        loc = (fact.location or "").lower()
        if loc.startswith("page "):
            try:
                return int(loc.split()[1])
            except (IndexError, ValueError):
                return None
        return None
    return offset // PAGE_CHARS


def highlight_on_page(
    filing: Filing,
    fact: Fact,
    page: int,
) -> dict[str, Any] | None:
    text = filing.body_text or ""
    chunk, start, _end = page_slice(text, page)
    if not fact.exact_quote:
        return None
    # Prefer absolute offset mapped into the page.
    abs_off = find_quote_offset(text, fact.exact_quote)
    if abs_off is not None and start <= abs_off < start + len(chunk):
        local = abs_off - start
        # Find end within the page chunk via local match when possible.
        local_in_chunk = find_quote_offset(chunk, fact.exact_quote)
        if local_in_chunk is not None:
            local = local_in_chunk
        quote = fact.exact_quote
        return {
            "fact_id": fact.id,
            "quote": quote,
            "start": local,
            "end": local + len(quote),
        }
    local = find_quote_offset(chunk, fact.exact_quote)
    if local is None:
        return None
    return {
        "fact_id": fact.id,
        "quote": fact.exact_quote,
        "start": local,
        "end": local + len(fact.exact_quote),
    }


def document_page_payload(
    filing: Filing,
    *,
    page: int,
    fact: Fact | None = None,
) -> dict[str, Any]:
    text = filing.body_text or ""
    pages = page_count(text)
    if pages == 0:
        pages = 1
    page_i = max(0, min(page, pages - 1))
    chunk, start, end = page_slice(text, page_i)
    highlight = highlight_on_page(filing, fact, page_i) if fact is not None else None
    return {
        "filing_id": filing.id,
        "title": filing.title,
        "form_type": filing.form_type,
        "url": filing.url,
        "filed_at": filing.filed_at,
        "page": page_i,
        "pages": pages,
        "page_chars": PAGE_CHARS,
        "start": start,
        "end": end,
        "text": chunk,
        "highlight": highlight,
    }


def prompt_version_for_run(session: Session, run: AgentRun) -> str | None:
    row = session.scalars(
        select(ModelCall)
        .where(ModelCall.run_id == run.id, ModelCall.prompt_version.is_not(None))
        .order_by(ModelCall.created_at.asc())
    ).first()
    if row is not None and row.prompt_version:
        return str(row.prompt_version)
    meta = run.result or {}
    if isinstance(meta.get("prompt_version"), str):
        return meta["prompt_version"]
    return None


def clamp_limit(limit: int, *, default: int = 50, maximum: int = 200) -> int:
    if limit <= 0:
        return default
    return min(limit, maximum)
