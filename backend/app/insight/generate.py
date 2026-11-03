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
from app.insight.facts import (
    build_impact_facts,
    build_refinancing_facts,
    build_snapshot_facts,
)
from app.llm.client import LLMClient, LLMError, StructuredOutputError
from app.llm.recorder import db_recorder
from app.models import Alert, Event, Issuer
from app.models.credit import CreditInsight

PROMPT_VERSION = "insight_v7"

SNAPSHOT_SYSTEM = (Path(__file__).resolve().parent / "prompts" / "insight_snapshot.md").read_text()
IMPACT_SYSTEM = (Path(__file__).resolve().parent / "prompts" / "insight_impact.md").read_text()
REFINANCING_SYSTEM = (
    Path(__file__).resolve().parent / "prompts" / "insight_refinancing.md"
).read_text()


class SnapshotTextModel(BaseModel):
    headline: str = Field(description="At most 12 words")
    snapshot: list[str] = Field(description="Exactly 3 sentences")


class ImpactTextModel(BaseModel):
    impact: str = Field(description="One sentence")


class RefinancingTextModel(BaseModel):
    sentence: str = Field(description="One sentence")


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
    # Serve only successful cache hits. Empty/error rows are retried so a
    # later facts fix (or a luckier model sample) can fill them in.
    if row is not None and row.input_hash == digest and row.status in {"complete", "partial"}:
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
    force: bool = False,
) -> CreditInsight:
    """Headline + snapshot sentences for an issuer at a date (cached)."""
    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise ValueError(f"unknown issuer: {issuer_key}")
    facts = build_snapshot_facts(session, issuer_key, as_of)
    digest = input_hash("snapshot", "", facts)
    if not force:
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
    if len(text.snapshot) != 3:
        summary["sentence_count_note"] = (
            f"model wrote {len(text.snapshot)} sentences (want 3); kept {len(kept)}"
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


def _impact_hook(facts: dict[str, Any]) -> str:
    """Short trailing clause from the alert title (term loan, notes, …)."""
    title = (facts.get("title") or facts.get("summary") or "").lower()
    if "term loan" in title:
        # Prefer a sized term-loan figure when present in facts.
        for key in ("term_loan_display", "new_borrowing_display"):
            sized = facts.get(key)
            if sized:
                return f" after it drew a {sized} term loan"
        return " after it drew a term loan"
    if "gbp" in title and "note" in title:
        return " after it closed a GBP notes offering"
    if "eur" in title and "note" in title:
        return " after it closed a euro notes offering"
    if "convertible" in title:
        return " after convertible notes"
    # "redem" matches redeem / redemption / redeemed (before bare "note").
    if "redem" in title:
        return " after the notes redemption"
    if "tender" in title:
        return " after the tender"
    if "note" in title:
        return " after the notes offering"
    return ""


def _material_wall_deltas(deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Buckets whose rounded display moved and abs change clears the floor."""
    from app.insight.display import MIN_WALL_CHANGE_ABS, format_usd_display

    out: list[dict[str, Any]] = []
    for d in deltas:
        before_d = d.get("before_display") or format_usd_display(d.get("before"))
        after_d = d.get("after_display") or format_usd_display(d.get("after"))
        if not before_d or not after_d or before_d == after_d:
            continue
        try:
            change_abs = abs(float(d.get("change_abs") or 0))
            if change_abs <= 0:
                change_abs = abs(float(d.get("after") or 0) - float(d.get("before") or 0))
        except (TypeError, ValueError):
            change_abs = 0.0
        if change_abs < MIN_WALL_CHANGE_ABS:
            continue
        out.append({**d, "before_display": before_d, "after_display": after_d})
    return out


def _template_impact(facts: dict[str, Any]) -> str | None:
    """Deterministic impact sentence from wall_deltas / before_after numbers.

    Lists every changed bucket (2026–2029 first, after 2030 last) with short
    company names. Every figure comes from the facts JSON ``*_display`` strings.
    """
    from app.insight.display import (
        bucket_sort_key,
        bucket_year_label,
        format_usd_display,
        issuer_short_name,
    )

    issuer = facts.get("issuer_short") or issuer_short_name(
        facts.get("issuer_key"), facts.get("issuer")
    )
    deltas = _material_wall_deltas(list(facts.get("wall_deltas") or []))
    parts: list[str] = []
    if deltas:
        ordered = sorted(deltas, key=lambda d: bucket_sort_key(d.get("bucket")))
        for d in ordered:
            before_d = d.get("before_display")
            after_d = d.get("after_display")
            try:
                rises = float(d.get("after") or 0) >= float(d.get("before") or 0)
            except (TypeError, ValueError):
                rises = True
            verb = "rises" if rises else "falls"
            year = bucket_year_label(d.get("bucket"))
            if year.startswith("after"):
                parts.append(f"{year} {verb} from {before_d} to {after_d}")
            else:
                parts.append(f"debt due in {year} {verb} from {before_d} to {after_d}")
    if not parts:
        for change in facts.get("before_after") or []:
            before_d = change.get("before_display") or format_usd_display(change.get("before"))
            after_d = change.get("after_display") or format_usd_display(change.get("after"))
            if not before_d or not after_d or before_d == after_d:
                continue
            field = change.get("bucket") or change.get("field") or "the figure"
            year = bucket_year_label(str(field))
            parts.append(f"debt due in {year} moves from {before_d} to {after_d}")
            break
    if not parts:
        return None
    if len(parts) == 1:
        body = parts[0]
    elif len(parts) == 2:
        body = f"{parts[0]}, and {parts[1]}"
    else:
        body = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    hook = _impact_hook(facts)
    # Possessive for natural English ("Amazon's debt due…").
    who = issuer if issuer.endswith("s") else f"{issuer}'s"
    if body.startswith("debt due") or body.startswith("after"):
        return f"{who} {body}{hook}."
    return f"{who} {body}{hook}."


async def generate_impact(
    session: Any,
    alert_id: str,
    *,
    client: Any | None = None,
    force: bool = False,
) -> CreditInsight:
    """One impact sentence for an alert (cached by alert + facts hash)."""
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"unknown alert: {alert_id}")
    event = session.get(Event, alert.event_id) if alert.event_id else None
    as_of = event.event_date if event and event.event_date else date.today()
    facts = build_impact_facts(session, alert_id)
    digest = input_hash("impact", alert_id, facts)
    if not force:
        hit = _cached(session, alert.issuer_id, as_of, "impact", alert_id, digest)
        if hit is not None:
            return hit
    dropped: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"check_prompt": CHECK_PROMPT_VERSION}
    kept: list[str] = []
    provider: str | None = None
    model: str | None = None
    # Prefer the deterministic wall template whenever buckets moved — it lists
    # every year in order with short names and 3-sig-fig displays. Templates are
    # already grounded in facts, so only the deterministic gate applies (same as
    # the brief); skip the flaky model verifier on ground-truth prose.
    template = _template_impact(facts)
    if template is not None:
        gate = check_text(template, facts)
        if gate.get("numbers_ok") and gate.get("advice_ok"):
            kept = [template]
            summary.update(
                {
                    "status": "checked",
                    "source": "template",
                    "verifier_models": [],
                }
            )
            provider, model = "template", "wall_deltas"
        else:
            dropped.append(
                {
                    "sentence": template,
                    "reason": "template failed deterministic gate",
                    "number_violations": gate.get("number_violations"),
                    "advice_hits": gate.get("advice_hits"),
                }
            )
    if not kept:
        text, provider, model, error = await _spark_text(
            IMPACT_SYSTEM, facts, ImpactTextModel, "insight_impact", client
        )
        if text is None or not isinstance(text, ImpactTextModel):
            dropped.append({"sentence": None, "reason": "generation failed", "problem": error})
            summary["status"] = "error"
        else:
            kept, m_dropped, gate_summary = await _gate_sentences(
                [text.impact.strip()], facts, client
            )
            dropped.extend(m_dropped)
            summary.update({"status": "checked", **gate_summary})
    status = "complete" if kept else ("error" if summary.get("status") == "error" else "empty")
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
        summary,
        provider,
        model,
        status,
    )


def _template_refinancing(facts: dict[str, Any]) -> str | None:
    """Deterministic +0bp sentence from panel numbers."""
    from app.insight.display import format_usd_display

    total = facts.get("base_incremental_annual_interest")
    tenor = facts.get("tenor_years")
    spread = facts.get("spread_pct")
    if total is None or tenor is None or spread is None:
        return None
    amount = facts.get("base_incremental_annual_interest_display") or format_usd_display(
        float(total)
    )
    return (
        f"Refinancing 2026–2029 debt at the {tenor}-year Treasury plus the issuer's "
        f"{spread:.2f}% spread would raise annual interest by about "
        f"{amount}/yr (illustrative)."
    )


async def generate_refinancing(
    session: Any,
    issuer_key: str,
    as_of: date,
    *,
    tenor_years: int = 5,
    client: Any | None = None,
) -> CreditInsight:
    """One checked +0bp refinancing sentence (cached). Display-only."""
    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise ValueError(f"unknown issuer: {issuer_key}")
    facts = build_refinancing_facts(session, issuer_key, as_of, tenor_years=tenor_years)
    digest = input_hash("refinancing", "0bp", facts)
    hit = _cached(session, issuer.id, as_of, "refinancing", "0bp", digest)
    if hit is not None:
        return hit

    summary: dict[str, Any] = {"status": "checked", "check_prompt": CHECK_PROMPT_VERSION}
    if facts.get("missing") or facts.get("base_incremental_annual_interest") is None:
        return _store(
            session,
            issuer.id,
            as_of,
            "refinancing",
            "0bp",
            digest,
            None,
            [],
            [
                {
                    "sentence": None,
                    "reason": facts.get("missing") or "rate-reset cost unavailable",
                }
            ],
            facts,
            {**summary, "status": "empty"},
            None,
            None,
            "empty",
        )

    text, provider, model, error = await _spark_text(
        REFINANCING_SYSTEM, facts, RefinancingTextModel, "insight_refinancing", client
    )
    kept: list[str] = []
    dropped: list[dict[str, Any]] = []
    if text is None or not isinstance(text, RefinancingTextModel):
        dropped.append({"sentence": None, "reason": "generation failed", "problem": error})
        summary["status"] = "error"
    else:
        kept, dropped, gate_summary = await _gate_sentences([text.sentence.strip()], facts, client)
        summary.update(gate_summary)

    if not kept:
        template = _template_refinancing(facts)
        if template is not None:
            t_kept, t_dropped, t_summary = await _gate_sentences([template], facts, client)
            if t_kept:
                kept = t_kept
                dropped = (
                    dropped
                    + [{"sentence": None, "reason": "fell back to template after model refusal"}]
                    + t_dropped
                )
                summary["fallback"] = "template"
                summary.update(t_summary)
                provider = provider or "template"
                model = model or "refinancing_panel"
            else:
                dropped.extend(t_dropped)

    status = "complete" if kept else ("error" if summary.get("status") == "error" else "empty")
    return _store(
        session,
        issuer.id,
        as_of,
        "refinancing",
        "0bp",
        digest,
        None,
        kept,
        dropped,
        facts,
        summary,
        provider,
        model,
        status,
    )


_SNAPSHOT_TILE_KEYS = (
    "total_debt",
    "due_24m",
    "leverage",
    "interest_coverage",
    "liquidity_coverage",
)


def assert_snapshot_matches_metric_tiles(
    session: Any, issuer_key: str, as_of: date, facts: dict[str, Any]
) -> None:
    """Raise when grounded snapshot facts disagree with current metric tiles (>1%)."""
    from app.api.credit import metric_tiles
    from app.models import Issuer

    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        return
    tiles = {t.metric: t for t in metric_tiles(session, issuer, as_of)}
    metric_facts = facts.get("metrics") or {}
    problems: list[str] = []
    for key in _SNAPSHOT_TILE_KEYS:
        fact = metric_facts.get(key) or {}
        fact_val = fact.get("value")
        tile = tiles.get(key)
        tile_val = float(tile.value) if tile and tile.value is not None else None
        if fact_val is None and tile_val is None:
            continue
        if fact_val is None or tile_val is None:
            problems.append(f"{key}: facts={fact_val} tile={tile_val}")
            continue
        base = abs(tile_val) if tile_val != 0 else abs(fact_val)
        if base == 0:
            continue
        if abs(float(fact_val) - tile_val) / base > 0.01:
            problems.append(f"{key}: facts={fact_val} tile={tile_val}")
    if problems:
        raise ValueError("snapshot facts stale vs metric tiles: " + "; ".join(problems))
