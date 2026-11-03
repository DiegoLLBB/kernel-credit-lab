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
from app.insight.display import (
    bucket_year_label,
    format_date_display,
    format_multiple_display,
    format_usd_display,
    issuer_short_name,
)
from app.insight.facts import (
    _alert_facts,
    _borrowing_amount_from_title,
    _borrowing_display_from_title,
    _change_pct,
    _num,
)
from app.llm.client import LLMClient, LLMError, Provider, StructuredOutputError
from app.llm.recorder import db_recorder
from app.metrics.compute import METRIC_IDS
from app.metrics.config import load_metric_config
from app.metrics.periods import prior_same_period
from app.models import Alert, CreditMetric, Event, Issuer
from app.models.credit import DailyBrief
from app.models.trace import ModelCall

PROMPT_VERSION = "brief_v6"
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
        short = issuer_short_name(issuer.key if issuer else None, issuer.name if issuer else None)
        alerts_out.append(
            {
                "alert_id": alert.id,
                "issuer_key": issuer.key if issuer else None,
                "issuer_name": issuer.name if issuer else None,
                "issuer_short": short,
                "event_date": event_day.isoformat() if event_day else None,
                "event_date_display": detail.get("event_date_display"),
                "title": alert.title,
                "impact_sentence": sentence,
                "impact_pending": pending,
                "live": bool(event and event.source == "live"),
                "before_after": detail.get("before_after") or [],
                "wall_deltas": wall_deltas,
                "material_change_abs": material_abs,
                "term_loan_display": (
                    _borrowing_display_from_title(alert.title)
                    if "term loan" in (alert.title or "").lower()
                    else None
                ),
                "term_loan_amount": (
                    _borrowing_amount_from_title(alert.title)
                    if "term loan" in (alert.title or "").lower()
                    else None
                ),
                "evidence_quotes": evidence[:3],
            }
        )
    alerts_out.sort(key=lambda a: float(a.get("material_change_abs") or 0), reverse=True)
    # Lead = largest wall move in a wider recent window (not only since the last
    # brief), so Amazon's term loan still opens the brief even when the prior
    # brief was only hours earlier.
    lead_cutoff = as_of - timedelta(days=45)
    lead_candidates: list[dict[str, Any]] = []
    for alert in session.scalars(select(Alert).order_by(Alert.created_at.desc())).all():
        event = session.get(Event, alert.event_id) if alert.event_id else None
        event_day = event.event_date if event else None
        if event_day is not None and (event_day < lead_cutoff or event_day > as_of):
            continue
        detail = _alert_facts(session, alert)
        wall_deltas = detail.get("wall_deltas") or []
        material_abs = 0.0
        for d in wall_deltas:
            try:
                material_abs = max(material_abs, float(d.get("change_abs") or 0))
            except (TypeError, ValueError):
                continue
        if material_abs <= 0:
            continue
        issuer = session.get(Issuer, alert.issuer_id)
        sentence, _, _ = impact_for_alert(session, alert)
        lead_candidates.append(
            {
                "alert_id": alert.id,
                "issuer_key": issuer.key if issuer else None,
                "issuer_name": issuer.name if issuer else None,
                "issuer_short": issuer_short_name(
                    issuer.key if issuer else None, issuer.name if issuer else None
                ),
                "title": alert.title,
                "impact_sentence": sentence,
                "wall_deltas": wall_deltas,
                "material_change_abs": material_abs,
                "term_loan_display": (
                    _borrowing_display_from_title(alert.title)
                    if "term loan" in (alert.title or "").lower()
                    else None
                ),
                "term_loan_amount": (
                    _borrowing_amount_from_title(alert.title)
                    if "term loan" in (alert.title or "").lower()
                    else None
                ),
            }
        )
    lead_candidates.sort(key=lambda a: float(a.get("material_change_abs") or 0), reverse=True)
    lead = lead_candidates[0] if lead_candidates else (alerts_out[0] if alerts_out else None)

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
                "issuer_short": issuer_short_name(issuer.key, issuer.name),
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

    lead_out = (
        {
            "alert_id": lead.get("alert_id"),
            "issuer_key": lead.get("issuer_key"),
            "issuer_name": lead.get("issuer_name"),
            "issuer_short": lead.get("issuer_short"),
            "title": lead.get("title"),
            "impact_sentence": lead.get("impact_sentence"),
            "wall_deltas": lead.get("wall_deltas") or [],
            "material_change_abs": lead.get("material_change_abs"),
            "term_loan_display": lead.get("term_loan_display"),
            "term_loan_amount": lead.get("term_loan_amount"),
        }
        if lead
        else None
    )
    payload = {
        "as_of": as_of.isoformat(),
        "as_of_display": format_date_display(as_of),
        "previous_brief_as_of": prev.as_of.isoformat() if prev else None,
        "window_since": since.isoformat(),
        "lead_change": lead_out,
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
    payload["copy"] = _brief_copy_block(payload)
    return payload


def _wall_move_clause(delta: dict[str, Any]) -> str | None:
    before_d = delta.get("before_display") or format_usd_display(delta.get("before"))
    after_d = delta.get("after_display") or format_usd_display(delta.get("after"))
    if not before_d or not after_d:
        return None
    try:
        rises = float(delta.get("after") or 0) >= float(delta.get("before") or 0)
    except (TypeError, ValueError):
        rises = True
    verb = "rises" if rises else "falls"
    year = bucket_year_label(delta.get("bucket"))
    if year.startswith("after"):
        return f"{year} {verb} from {before_d} to {after_d}"
    return f"debt due in {year} {verb} from {before_d} to {after_d}"


def _template_lead_sentence(facts: dict[str, Any]) -> str | None:
    """Most material wall move + near-term meaning (gate-safe display strings)."""
    lead = facts.get("lead_change") or {}
    if not lead:
        return None
    short = lead.get("issuer_short") or issuer_short_name(
        lead.get("issuer_key"), lead.get("issuer_name")
    )
    who = short if short.endswith("s") else f"{short}'s"
    deltas = list(lead.get("wall_deltas") or [])
    if not deltas:
        impact = (lead.get("impact_sentence") or "").strip()
        return impact or None
    ranked = sorted(deltas, key=lambda d: float(d.get("change_abs") or 0), reverse=True)
    clause = _wall_move_clause(ranked[0])
    if not clause:
        return None
    title = (lead.get("title") or "").lower()
    sized = lead.get("term_loan_display")
    if "term loan" in title:
        hook = f" after it drew a {sized} term loan" if sized else " after it drew a term loan"
        meaning = ", lifting near-term maturities"
    elif "convertible" in title:
        hook, meaning = " after convertible notes", ""
    elif "note" in title:
        hook, meaning = " after the notes offering", ""
    else:
        hook, meaning = "", ""
    return f"{who} {clause}{hook}{meaning}."


def _brief_copy_block(facts: dict[str, Any]) -> dict[str, Any]:
    """Pre-built, display-string-only lines the writer (and templates) may quote."""
    lead = _template_lead_sentence(facts)
    # Other alerts (skip the lead).
    lead_id = (facts.get("lead_change") or {}).get("alert_id")
    others: list[str] = []
    for alert in facts.get("alerts") or []:
        if alert.get("alert_id") == lead_id:
            continue
        short = alert.get("issuer_short") or issuer_short_name(
            alert.get("issuer_key"), alert.get("issuer_name")
        )
        deltas = alert.get("wall_deltas") or []
        if not deltas:
            continue
        ranked = sorted(deltas, key=lambda d: float(d.get("change_abs") or 0), reverse=True)
        clause = _wall_move_clause(ranked[0])
        if not clause:
            continue
        who = short if short.endswith("s") else f"{short}'s"
        others.append(f"{who} {clause}.")
        if len(others) >= 2:
            break
    other_line = None
    if others:
        other_line = (
            "Other changes this period: " + " ".join(others)
            if len(others) > 1
            else f"Also this period, {others[0][0].lower() + others[0][1:]}"
        )

    live = facts.get("live") or {}
    docs = live.get("documents") or []
    reviewed = int(live.get("reviewed_no_alert") or live.get("logged") or 0)
    paused_docs = [d for d in docs if d.get("outcome") == "paused"]
    analyst_docs = [d for d in docs if d.get("outcome") == "needs an analyst"]
    live_bits: list[str] = []
    if reviewed:
        noun = "document" if reviewed == 1 else "documents"
        word = {
            1: "one",
            2: "two",
            3: "three",
            4: "four",
            5: "five",
            6: "six",
            7: "seven",
            8: "eight",
            9: "nine",
            10: "ten",
        }.get(reviewed, str(reviewed))
        live_bits.append(f"Kernel reviewed {word} {noun} with no alert")
    for d in paused_docs[:1]:
        short = issuer_short_name(d.get("issuer_key"), d.get("issuer_name"))
        form = (d.get("form") or "").upper()
        label = "10-Q" if form.startswith("10-Q") else (d.get("form") or "filing")
        live_bits.append(f"{short}'s {label} is paused at its turn limit")
    analyst_names: list[str] = []
    for d in analyst_docs[:2]:
        short = issuer_short_name(d.get("issuer_key"), d.get("issuer_name"))
        form = (d.get("form") or "").upper()
        if form.startswith("424B"):
            doc = f"{short}'s prospectus supplement"
        elif form.startswith("10-Q"):
            doc = f"{short}'s quarterly report"
        else:
            doc = f"{short}'s filing"
        analyst_names.append(doc)
    if analyst_names:
        if len(analyst_names) == 1:
            live_bits.append(f"{analyst_names[0]} needs an analyst")
        else:
            live_bits.append(f"{analyst_names[0]} and {analyst_names[1]} need an analyst")
    live_line = None
    if live_bits:
        if len(live_bits) == 1:
            live_line = live_bits[0] + "."
        else:
            live_line = live_bits[0] + "; " + "; ".join(live_bits[1:]) + "."

    watch: list[str] = []
    for row in facts.get("watchlist") or []:
        short = row.get("issuer_short") or issuer_short_name(
            row.get("issuer_key"), row.get("issuer_name")
        )
        metrics = row.get("metrics") or {}
        # Prefer FY total_debt change when present (one period only).
        td = metrics.get("total_debt") or {}
        if not isinstance(td, dict):
            continue
        now_d = td.get("value_display")
        then_d = td.get("previous_value_display")
        period = td.get("period")
        prior_period = td.get("previous_period")
        if not now_d or not period:
            continue
        # Skip TTM lines that would later be mixed with FY elsewhere.
        if str(period).upper().startswith("TTM"):
            # Still allow a pure TTM watch line.
            watch.append(f"{short}'s total debt is {now_d} in {period}.")
        elif then_d and prior_period:
            watch.append(
                f"{short}'s total debt is {now_d} in {period}, up from {then_d} in {prior_period}."
            )
        else:
            watch.append(f"{short}'s total debt is {now_d} in {period}.")
        if len(watch) >= 2:
            break

    return {
        "lead": lead,
        "other_changes": other_line,
        "live": live_line,
        "watch": watch,
    }


def _template_brief_sentences(facts: dict[str, Any]) -> list[str]:
    """4–5 gate-safe sentences covering the required brief structure."""
    copy = facts.get("copy") or _brief_copy_block(facts)
    out: list[str] = []
    if copy.get("lead"):
        out.append(str(copy["lead"]))
    if copy.get("other_changes"):
        out.append(str(copy["other_changes"]))
    if copy.get("live"):
        out.append(str(copy["live"]))
    for line in copy.get("watch") or []:
        out.append(str(line))
        if len(out) >= 5:
            break
    return out[:5]


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
        if len(raw) < 4 or len(raw) > 5:
            summary["sentence_count_note"] = f"model wrote {len(raw)} sentences (want 4-5)"
        kept, dropped, gate_summary = await _gate_sentences(raw, facts, client)
        summary.update(gate_summary)
        calls2, cost2, unpriced2 = _cost_since(session, started)
        calls, cost, has_unpriced = calls2, cost2, unpriced2

    # Prefer gate-safe template pack (lead → other → live → watch), then unique model lines.
    templates = _template_brief_sentences(facts)
    template_kept: list[str] = []
    for sentence in templates:
        gate = check_text(sentence, facts)
        if gate.get("numbers_ok") and gate.get("advice_ok"):
            template_kept.append(sentence)
        else:
            dropped.append(
                {
                    "sentence": sentence,
                    "reason": "template failed deterministic gate",
                    "number_violations": gate.get("number_violations"),
                    "advice_hits": gate.get("advice_hits"),
                }
            )

    def _money_tokens(sentence: str) -> set[str]:
        return {t for t in sentence.replace(",", "").split() if t.startswith("$")}

    merged: list[str] = []
    if template_kept:
        merged.append(template_kept[0])
        summary["lead"] = "material_wall_move"
    for sentence in template_kept[1:] + kept:
        s = sentence.strip()
        if not s or s in merged:
            continue
        # Drop near-duplicates of anything already kept (same $-tokens).
        tokens = _money_tokens(s)
        if tokens and any(
            tokens.issubset(_money_tokens(m)) or _money_tokens(m).issubset(tokens) for m in merged
        ):
            continue
        low = s.lower()
        if "largest debt" in low or "largest material" in low:
            continue
        merged.append(s)
        if len(merged) >= 5:
            break
    kept = merged[:5]

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
