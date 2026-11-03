"""Muse Wonder daily brief across the watchlist (display-only, claims-checked).

Facts JSON → Wonder (`DEEP_RESEARCH_MODEL`) → Spark Open verifier (same gates as
snapshots). Cap $0.50 per brief. Never read by the agent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.alerts import impact_for_alert
from app.config import get_settings
from app.ingest.live import live_stats
from app.insight.check import (
    PROMPT_VERSION as CHECK_PROMPT_VERSION,
)
from app.insight.check import (
    check_text,
    verify_sentences,
)
from app.insight.display import format_date_display, format_multiple_display, format_usd_display
from app.insight.facts import _alert_facts, _change_pct, _num
from app.llm.client import LLMClient, LLMError, Provider, StructuredOutputError
from app.llm.recorder import db_recorder
from app.metrics.compute import METRIC_IDS
from app.metrics.config import load_metric_config
from app.metrics.periods import prior_same_period
from app.models import Alert, CreditMetric, Event, Issuer
from app.models.credit import DailyBrief
from app.models.trace import ModelCall

PROMPT_VERSION = "brief_v2"
BRIEF_COST_CAP_USD = Decimal("0.50")
BRIEF_SYSTEM = (Path(__file__).resolve().parent / "prompts" / "insight_brief.md").read_text()

_WATCH_METRICS = (
    "total_debt",
    "due_24m",
    "liquidity_coverage",
    "leverage",
    "interest_coverage",
)


class BriefTextModel(BaseModel):
    brief: list[str] = Field(description="4 to 6 sentences")


def input_hash(facts: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"v": PROMPT_VERSION, "scope": "brief", "facts": facts},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _previous_brief(session: Any, as_of: date) -> DailyBrief | None:
    return session.scalars(
        select(DailyBrief)
        .where(DailyBrief.as_of < as_of, DailyBrief.status.in_(("complete", "partial")))
        .order_by(DailyBrief.as_of.desc())
    ).first()


def build_brief_facts(session: Any, as_of: date) -> dict[str, Any]:
    """Portfolio facts JSON — the only numbers Wonder may quote."""
    prev = _previous_brief(session, as_of)
    since = prev.as_of if prev is not None else as_of - timedelta(days=7)

    alerts_out: list[dict[str, Any]] = []
    for alert in session.scalars(select(Alert).order_by(Alert.created_at.desc())).all():
        event = session.get(Event, alert.event_id) if alert.event_id else None
        event_day = event.event_date if event else None
        # Window: events since previous brief day (or 7 days), through as_of.
        if event_day is not None and (event_day < since or event_day > as_of):
            continue
        if event_day is None and alert.created_at is not None:
            created_day = alert.created_at.date()
            if created_day < since or created_day > as_of:
                continue
        issuer = session.get(Issuer, alert.issuer_id)
        sentence, pending, _ = impact_for_alert(session, alert)
        detail = _alert_facts(session, alert)
        evidence: list[str] = []
        for finding in detail.get("findings") or []:
            for quote in finding.get("quotes") or []:
                evidence.append(str(quote)[:400])
        wall_deltas = detail.get("wall_deltas") or []
        material_abs = 0.0
        for d in wall_deltas:
            try:
                material_abs = max(material_abs, float(d.get("change_abs") or 0))
            except (TypeError, ValueError):
                continue
        alerts_out.append(
            {
                "alert_id": alert.id,
                "issuer_key": issuer.key if issuer else None,
                "issuer_name": issuer.name if issuer else None,
                "event_date": event_day.isoformat() if event_day else None,
                "event_date_display": detail.get("event_date_display"),
                "title": alert.title,
                "impact_sentence": sentence,
                "impact_pending": pending,
                "live": bool(event and event.source == "live"),
                "before_after": detail.get("before_after") or [],
                "wall_deltas": wall_deltas,
                "material_change_abs": material_abs,
                "evidence_quotes": evidence[:3],
            }
        )
    alerts_out.sort(key=lambda a: float(a.get("material_change_abs") or 0), reverse=True)
    lead = alerts_out[0] if alerts_out else None

    cfg = load_metric_config()
    watchlist: list[dict[str, Any]] = []
    for key in cfg.issuers:
        issuer = session.scalar(select(Issuer).where(Issuer.key == key))
        if issuer is None:
            continue
        metrics: dict[str, Any] = {}
        for metric in _WATCH_METRICS:
            if metric not in METRIC_IDS:
                continue
            rows = session.scalars(
                select(CreditMetric)
                .where(
                    CreditMetric.issuer_id == issuer.id,
                    CreditMetric.metric == metric,
                    CreditMetric.filed <= as_of,
                )
                .order_by(CreditMetric.period_end.asc())
            ).all()
            if not rows:
                metrics[metric] = {"value": None, "missing": "no data yet"}
                continue
            latest = rows[-1]
            prior = prior_same_period(rows, latest)
            now_val = _num(latest.value)
            then_val = _num(prior.value) if prior else None
            unit = latest.unit
            if unit == "USD":
                now_disp = format_usd_display(now_val)
                then_disp = format_usd_display(then_val)
            elif unit in {"x", "multiple", "ratio"}:
                now_disp = format_multiple_display(now_val)
                then_disp = format_multiple_display(then_val)
            else:
                now_disp = str(now_val) if now_val is not None else None
                then_disp = str(then_val) if then_val is not None else None
            metrics[metric] = {
                "value": now_val,
                "value_display": now_disp,
                "unit": unit,
                "period": latest.fiscal_label,
                "previous_value": then_val,
                "previous_value_display": then_disp,
                "previous_period": prior.fiscal_label if prior else None,
                "change_pct": _change_pct(now_val, then_val),
                "change_pct_abs": abs(_change_pct(now_val, then_val) or 0)
                if _change_pct(now_val, then_val) is not None
                else None,
            }
        watchlist.append(
            {
                "issuer_key": issuer.key,
                "issuer_name": issuer.name,
                "metrics": metrics,
            }
        )

    stats = live_stats(session)
    live_docs = []
    for doc in stats.get("documents") or []:
        live_docs.append(
            {
                "issuer_key": doc.get("issuer_key"),
                "issuer_name": doc.get("issuer_name"),
                "form": doc.get("form"),
                "event_date": doc.get("event_date"),
                "outcome": doc.get("outcome"),
                "reason": doc.get("reason"),
                "alert_id": doc.get("alert_id"),
            }
        )

    return {
        "as_of": as_of.isoformat(),
        "as_of_display": format_date_display(as_of),
        "previous_brief_as_of": prev.as_of.isoformat() if prev else None,
        "window_since": since.isoformat(),
        "lead_change": (
            {
                "alert_id": lead.get("alert_id"),
                "issuer_key": lead.get("issuer_key"),
                "issuer_name": lead.get("issuer_name"),
                "title": lead.get("title"),
                "impact_sentence": lead.get("impact_sentence"),
                "wall_deltas": lead.get("wall_deltas") or [],
                "material_change_abs": lead.get("material_change_abs"),
            }
            if lead
            else None
        ),
        "alerts": alerts_out,
        "watchlist": watchlist,
        "live": {
            "go_live_at": stats.get("go_live_at"),
            "documents_read": int(stats.get("documents_read") or 0),
            "raised_alert": int(stats.get("alerted") or 0),
            "reviewed_no_alert": int(stats.get("logged") or 0),
            "needs_an_analyst": int(stats.get("refused") or 0),
            "paused": int(stats.get("paused") or 0),
            "failed": int(stats.get("failed") or 0),
            # Keep legacy keys for older readers / tests.
            "alerted": int(stats.get("alerted") or 0),
            "logged": int(stats.get("logged") or 0),
            "refused": int(stats.get("refused") or 0),
            "documents": live_docs,
        },
        "alert_count": len(alerts_out),
        "watchlist_count": len(watchlist),
    }


def _annotate_links(sentences: list[str], facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Map numbers in kept sentences to alert or issuer URLs."""
    linkables: list[tuple[str, str, str]] = []  # (needle, href, kind)
    for alert in facts.get("alerts") or []:
        aid = alert.get("alert_id")
        if not aid:
            continue
        href = f"/alerts/{aid}"
        for ba in alert.get("before_after") or []:
            for key in ("before", "after", "change_pct"):
                val = ba.get(key)
                if val is None:
                    continue
                linkables.append((str(val), href, "alert"))
        name = alert.get("issuer_name") or alert.get("issuer_key")
        if name:
            linkables.append((str(name), href, "alert"))
    for row in facts.get("watchlist") or []:
        key = row.get("issuer_key")
        if not key:
            continue
        href = f"/issuers/{key}"
        for metric, detail in (row.get("metrics") or {}).items():
            if not isinstance(detail, dict):
                continue
            for field in ("value", "previous_value", "change_pct", "change_pct_abs"):
                val = detail.get(field)
                if val is None:
                    continue
                linkables.append((str(val), href, "metric"))
            linkables.append((metric, href, "metric"))
        if row.get("issuer_name"):
            linkables.append((str(row["issuer_name"]), href, "metric"))

    out: list[dict[str, Any]] = []
    for sentence in sentences:
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for needle, href, kind in linkables:
            if len(needle) < 2:
                continue
            if needle in sentence and href not in seen:
                refs.append({"href": href, "kind": kind, "match": needle[:48]})
                seen.add(href)
        out.append({"sentence": sentence, "refs": refs})
    return out


async def _gate_sentences(
    sentences: list[str], facts: dict[str, Any], client: Any | None
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
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


def _cost_since(session: Any, started_at: Any) -> tuple[int, Decimal | None, bool]:
    """Return (call_count, priced_total_or_none, has_unpriced_calls).

    Muse Wonder has no published $/token — never report $0 for a Wonder-only brief.
    """
    from app.llm.pricing import aggregate_call_costs

    rows = list(
        session.scalars(
            select(ModelCall).where(
                ModelCall.task.in_(("insight_brief", "insight_check")),
                ModelCall.created_at >= started_at,
            )
        ).all()
    )
    total, has_unpriced, _label = aggregate_call_costs(rows)
    return len(rows), total, has_unpriced


async def generate_daily_brief(
    session: Any,
    as_of: date | None = None,
    *,
    client: Any | None = None,
    force: bool = False,
) -> DailyBrief:
    """Write / refresh the checked daily brief for ``as_of`` (default today)."""
    as_of = as_of or date.today()
    facts = build_brief_facts(session, as_of)
    digest = input_hash(facts)
    existing = session.scalar(select(DailyBrief).where(DailyBrief.as_of == as_of))
    if (
        not force
        and existing is not None
        and existing.input_hash == digest
        and existing.status in {"complete", "partial"}
    ):
        return existing

    settings = get_settings()
    wonder = settings.deep_research_model or "muse-wonder-1.0"
    local = client
    close = False
    if local is None:
        local = LLMClient(recorder=db_recorder)
        close = True

    started = session.scalar(select(func.now()))
    provider: str | None = None
    model: str | None = None
    error: str | None = None
    text: BriefTextModel | None = None
    try:
        messages = [
            {"role": "system", "content": BRIEF_SYSTEM},
            {
                "role": "user",
                "content": "Write from these computed facts only. Input JSON follows.\n\n"
                + json.dumps(facts, ensure_ascii=False, default=str),
            },
        ]
        try:
            parsed, _llm = await local.structured_json(
                provider=Provider.META,
                model=wonder,
                messages=messages,
                schema=BriefTextModel,
                task="insight_brief",
                prompt_version=PROMPT_VERSION,
                temperature=0.0,
                run_id=None,
            )
            text = parsed if isinstance(parsed, BriefTextModel) else None
            provider, model = str(Provider.META), wonder
        except (LLMError, StructuredOutputError, OSError, TimeoutError, RuntimeError) as exc:
            error = str(exc)[:300]
    finally:
        if close and hasattr(local, "aclose"):
            await local.aclose()

    calls, cost, has_unpriced = _cost_since(session, started)
    if cost is not None and cost > BRIEF_COST_CAP_USD:
        # Soft stop: still store whatever we have, but mark the overage.
        pass

    kept: list[str] = []
    dropped: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "status": "checked",
        "check_prompt": CHECK_PROMPT_VERSION,
        "cost_cap_usd": float(BRIEF_COST_CAP_USD),
    }
    if text is None:
        dropped.append({"sentence": None, "reason": "generation failed", "problem": error})
        summary["status"] = "error"
    else:
        raw = [s.strip() for s in text.brief if s and s.strip()]
        if len(raw) < 4 or len(raw) > 6:
            summary["sentence_count_note"] = f"model wrote {len(raw)} sentences (want 4-6)"
        kept, dropped, gate_summary = await _gate_sentences(raw, facts, client)
        summary.update(gate_summary)
        calls2, cost2, unpriced2 = _cost_since(session, started)
        calls, cost, has_unpriced = calls2, cost2, unpriced2

    if cost is not None and cost > BRIEF_COST_CAP_USD:
        summary["cost_cap_exceeded"] = True
    if has_unpriced:
        summary["unpriced_calls"] = True

    links = _annotate_links(kept, facts)
    if kept and len(kept) >= 4:
        status = "complete"
    elif kept:
        status = "partial"
    elif summary.get("status") == "error":
        status = "error"
    else:
        status = "empty"

    payload = {
        "input_hash": digest,
        "sentences": kept,
        "dropped": dropped,
        "facts": facts,
        "check": summary,
        "links": links,
        "status": status,
        "provider": provider,
        "model": model,
        "model_calls": calls,
        # Never persist $0 when Wonder ran unpriced — null means unknown.
        "cost_usd": cost,
    }
    if existing is None:
        row = DailyBrief(as_of=as_of, **payload)
        session.add(row)
    else:
        row = existing
        for key, value in payload.items():
            setattr(row, key, value)
    session.commit()
    return row


def latest_brief(session: Any) -> DailyBrief | None:
    return session.scalars(select(DailyBrief).order_by(DailyBrief.as_of.desc())).first()


def replay_last_day(session: Any) -> date | None:
    """Last calendar day of the seeded replay (for one-shot brief)."""
    return session.scalar(select(func.max(Event.event_date)).where(Event.source != "live"))
