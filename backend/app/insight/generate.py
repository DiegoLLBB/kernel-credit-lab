"""Generate checked insight text with Muse Spark, cache by input hash.

``generate_snapshot`` (headline + 3–4 sentences) and ``generate_impact`` (one
sentence per alert) share the pipeline: build facts → hash inputs → serve from
``credit_insight`` on a hash hit (no model calls) → otherwise ask Spark for
structured text → drop any sentence failing the deterministic gates or the
independent verifier → store text, inputs, check result and model attributions
(the UI renders this as "how this was written").
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.claims import _provider_model_attempts
from app.config import get_settings
from app.insight.check import (
    PROMPT_VERSION as CHECK_PROMPT_VERSION,
)
from app.insight.check import (
    check_headline,
    check_text,
    verify_sentences,
)
from app.insight.facts import build_impact_facts, build_snapshot_facts
from app.llm.client import LLMClient, LLMError, StructuredOutputError
from app.llm.recorder import db_recorder
from app.models import Alert, Event, Issuer
from app.models.credit import CreditInsight

PROMPT_VERSION = "insight_v1"

SNAPSHOT_SYSTEM = (Path(__file__).resolve().parent / "prompts" / "insight_snapshot.md").read_text()
IMPACT_SYSTEM = (Path(__file__).resolve().parent / "prompts" / "insight_impact.md").read_text()


class SnapshotTextModel(BaseModel):
    headline: str = Field(description="At most 12 words")
    snapshot: list[str] = Field(description="3 to 4 sentences")


class ImpactTextModel(BaseModel):
    impact: str = Field(description="One sentence")


def input_hash(scope: str, ref: str, facts: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"v": PROMPT_VERSION, "scope": scope, "ref": ref, "facts": facts},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cached(
    session: Any, issuer_id: str, as_of: date, scope: str, ref: str, digest: str
) -> CreditInsight | None:
    row = session.scalar(
        select(CreditInsight).where(
            CreditInsight.issuer_id == issuer_id,
            CreditInsight.as_of == as_of,
            CreditInsight.scope == scope,
            CreditInsight.ref == ref,
        )
    )
    if row is not None and row.input_hash == digest:
        return row
    return None


def _store(
    session: Any,
    issuer_id: str,
    as_of: date,
    scope: str,
    ref: str,
    digest: str,
    headline: str | None,
    kept: list[str],
    dropped: list[dict[str, Any]],
    facts: dict[str, Any],
    check: dict[str, Any],
    provider: str | None,
    model: str | None,
    status: str,
) -> CreditInsight:
    row = session.scalar(
        select(CreditInsight).where(
            CreditInsight.issuer_id == issuer_id,
            CreditInsight.as_of == as_of,
            CreditInsight.scope == scope,
            CreditInsight.ref == ref,
        )
    )
    payload = {
        "input_hash": digest,
        "headline": headline,
        "sentences": kept,
        "dropped": dropped,
        "facts": facts,
        "check": check,
        "provider": provider,
        "model": model,
        "status": status,
    }
    if row is None:
        row = CreditInsight(issuer_id=issuer_id, as_of=as_of, scope=scope, ref=ref, **payload)
        session.add(row)
    else:
        for key, value in payload.items():
            setattr(row, key, value)
    session.commit()
    return row


async def _spark_text(
    system: str,
    facts: dict[str, Any],
    schema: type[BaseModel],
    task: str,
    client: Any | None,
) -> tuple[BaseModel | None, str | None, str | None, str | None]:
    """Ask Spark for structured text; (text, provider, model, error)."""
    local = client
    close = False
    if local is None:
        local = LLMClient(recorder=db_recorder)
        close = True
    try:
        settings = getattr(local, "settings", None) or get_settings()
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "Write from these computed facts only. Input JSON follows.\n\n"
                + json.dumps(facts, ensure_ascii=False, default=str),
            },
        ]
        last_error: str | None = None
        for provider, model in _provider_model_attempts(settings):
            try:
                text, _llm = await local.structured_json(
                    provider=provider,
                    model=model,
                    messages=messages,
                    schema=schema,
                    task=task,
                    prompt_version=PROMPT_VERSION,
                    temperature=0.0,
                    run_id=None,
                )
                return text, str(provider), model, None
            except (LLMError, StructuredOutputError, OSError, TimeoutError, RuntimeError) as exc:
                last_error = str(exc)[:300]
                continue
        return None, None, None, last_error or "no model available"
    finally:
        if close and hasattr(local, "aclose"):
            await local.aclose()


async def _gate_sentences(
    sentences: list[str], facts: dict[str, Any], client: Any | None
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """Run both gates; returns (kept, dropped, check_summary)."""
    dropped: list[dict[str, Any]] = []
    survivors: list[str] = []
    for sentence in sentences:
        gate = check_text(sentence, facts)
        if not gate["numbers_ok"] or not gate["advice_ok"]:
            dropped.append(
                {
                    "sentence": sentence,
                    "reason": "deterministic gate",
                    "number_violations": gate["number_violations"],
                    "advice_hits": gate["advice_hits"],
                }
            )
        else:
            survivors.append(sentence)
    verdicts = await verify_sentences(survivors, facts, client=client)
    kept: list[str] = []
    models_used: list[str] = []
    for verdict in verdicts:
        if verdict["status"] == "supported":
            kept.append(verdict["sentence"])
        else:
            dropped.append(
                {
                    "sentence": verdict["sentence"],
                    "reason": f"independent verifier: {verdict['status']}",
                    "phrase": verdict.get("phrase"),
                    "problem": verdict.get("problem"),
                }
            )
        if verdict.get("model"):
            models_used.append(str(verdict["model"]))
    return kept, dropped, {"verifier_models": sorted(set(models_used))}


async def generate_snapshot(
    session: Any,
    issuer_key: str,
    as_of: date,
    *,
    client: Any | None = None,
) -> CreditInsight:
    """Headline + snapshot sentences for an issuer at a date (cached)."""
    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise ValueError(f"unknown issuer: {issuer_key}")
    facts = build_snapshot_facts(session, issuer_key, as_of)
    digest = input_hash("snapshot", "", facts)
    hit = _cached(session, issuer.id, as_of, "snapshot", "", digest)
    if hit is not None:
        return hit
    text, provider, model, error = await _spark_text(
        SNAPSHOT_SYSTEM, facts, SnapshotTextModel, "insight_snapshot", client
    )
    if text is None or not isinstance(text, SnapshotTextModel):
        return _store(
            session,
            issuer.id,
            as_of,
            "snapshot",
            "",
            digest,
            None,
            [],
            [{"sentence": None, "reason": "generation failed", "problem": error}],
            facts,
            {"status": "error", "check_prompt": CHECK_PROMPT_VERSION},
            provider,
            model,
            "error",
        )
    assert isinstance(text, SnapshotTextModel)
    headline: str | None = text.headline.strip()
    headline_check = check_headline(headline, facts)
    headline_dropped: list[dict[str, Any]] = []
    if (
        not headline_check["headline_ok"]
        or not headline_check["numbers_ok"]
        or not headline_check["advice_ok"]
    ):
        headline_dropped.append(
            {
                "sentence": headline,
                "reason": "headline gate",
                "words": headline_check["headline_words"],
                "number_violations": headline_check["number_violations"],
                "advice_hits": headline_check["advice_hits"],
            }
        )
        headline = None
    kept, dropped, summary = await _gate_sentences(text.snapshot, facts, client)
    dropped = headline_dropped + dropped
    if len(text.snapshot) < 3 or len(text.snapshot) > 5:
        summary["sentence_count_note"] = (
            f"model wrote {len(text.snapshot)} sentences (want 3-4); kept {len(kept)}"
        )
    if kept and headline is not None:
        status = "complete"
    elif kept or headline is not None:
        status = "partial"
    else:
        status = "empty"
    return _store(
        session,
        issuer.id,
        as_of,
        "snapshot",
        "",
        digest,
        headline,
        kept,
        dropped,
        facts,
        {"status": "checked", "check_prompt": CHECK_PROMPT_VERSION, **summary},
        provider,
        model,
        status,
    )


async def generate_impact(
    session: Any,
    alert_id: str,
    *,
    client: Any | None = None,
) -> CreditInsight:
    """One impact sentence for an alert (cached by alert + facts hash)."""
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"unknown alert: {alert_id}")
    event = session.get(Event, alert.event_id) if alert.event_id else None
    as_of = event.event_date if event and event.event_date else date.today()
    facts = build_impact_facts(session, alert_id)
    digest = input_hash("impact", alert_id, facts)
    hit = _cached(session, alert.issuer_id, as_of, "impact", alert_id, digest)
    if hit is not None:
        return hit
    text, provider, model, error = await _spark_text(
        IMPACT_SYSTEM, facts, ImpactTextModel, "insight_impact", client
    )
    if text is None or not isinstance(text, ImpactTextModel):
        return _store(
            session,
            alert.issuer_id,
            as_of,
            "impact",
            alert_id,
            digest,
            None,
            [],
            [{"sentence": None, "reason": "generation failed", "problem": error}],
            facts,
            {"status": "error", "check_prompt": CHECK_PROMPT_VERSION},
            provider,
            model,
            "error",
        )
    assert isinstance(text, ImpactTextModel)
    kept, dropped, summary = await _gate_sentences([text.impact.strip()], facts, client)
    status = "complete" if kept else "empty"
    return _store(
        session,
        alert.issuer_id,
        as_of,
        "impact",
        alert_id,
        digest,
        None,
        kept,
        dropped,
        facts,
        {"status": "checked", "check_prompt": CHECK_PROMPT_VERSION, **summary},
        provider,
        model,
        status,
    )
