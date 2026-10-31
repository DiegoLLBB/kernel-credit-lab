"""Independent sentence verification: deterministic gates + Spark Open verifier.

Gate 1 (deterministic): :func:`check_text` runs the number matcher
(:mod:`app.insight.numbers`) and the no-advice phrase list. Exact, no model.
Gate 2 (model): :func:`verify_sentences` sends the surviving sentences plus
the facts JSON to the independent verifier — Spark Open first, Muse Spark as
fallback — reusing the claims-check provider path from :mod:`app.agent.claims`
(same models, same recording via ``db_recorder``). Fail-closed: a verifier
error drops the sentences it was meant to judge, and the refusal is stored.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.agent.claims import _provider_model_attempts
from app.config import get_settings
from app.insight.numbers import collect_numbers, find_violations
from app.llm.client import LLMClient, LLMError, StructuredOutputError
from app.llm.recorder import db_recorder

logger = logging.getLogger(__name__)

PROMPT_VERSION = "insight_check_v1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "insight_check.md"

BANNED_PHRASES = (
    "buy",
    "sell",
    "hold",
    "overweight",
    "underweight",
    "outperform",
    "upgrade",
    "downgrade",
    "recommend",
    "price target",
    "investment advice",
)

MAX_HEADLINE_WORDS = 12


class SentenceVerdictModel(BaseModel):
    sentence_index: int = Field(description="0-based index into the sentences list")
    status: str = Field(description="'supported' or 'unsupported'")
    unsupported_phrase: str | None = Field(
        description="Offending phrase when unsupported; else null"
    )


class InsightAssessmentModel(BaseModel):
    verdicts: list[SentenceVerdictModel]


def load_check_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def check_text(text: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Gate 1: number grounding + advice language (no model involved)."""
    registry = collect_numbers(facts)
    violations = find_violations(text, registry)
    lowered = text.lower()
    advice_hits = [
        p
        for p in BANNED_PHRASES
        if p in lowered and (len(p.split()) > 1 or re.search(rf"\b{re.escape(p)}\b", lowered))
    ]
    return {
        "numbers_ok": not violations,
        "number_violations": violations,
        "advice_ok": not advice_hits,
        "advice_hits": advice_hits,
    }


def check_headline(headline: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Headline gate: word count plus the standard text gates."""
    result = check_text(headline, facts)
    words = len(headline.split())
    result["headline_ok"] = words <= MAX_HEADLINE_WORDS
    result["headline_words"] = words
    return result


async def verify_sentences(
    sentences: list[str],
    facts: dict[str, Any],
    *,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Gate 2: independent model verdicts, one per sentence (fail-closed)."""
    if not sentences:
        return []
    local = client
    close = False
    if local is None:
        local = LLMClient(recorder=db_recorder)
        close = True
    try:
        settings = getattr(local, "settings", None) or get_settings()
        messages = [
            {"role": "system", "content": load_check_prompt()},
            {
                "role": "user",
                "content": "Verify each sentence against the facts. Input JSON follows.\n\n"
                + json.dumps({"sentences": sentences, "facts": facts}, ensure_ascii=False),
            },
        ]
        assessment: InsightAssessmentModel | None = None
        used_provider: str | None = None
        used_model: str | None = None
        last_error: str | None = None
        for provider, model in _provider_model_attempts(settings):
            try:
                assessment, _llm = await local.structured_json(
                    provider=provider,
                    model=model,
                    messages=messages,
                    schema=InsightAssessmentModel,
                    task="insight_check",
                    prompt_version=PROMPT_VERSION,
                    temperature=0.0,
                    run_id=None,
                )
                used_provider = str(provider)
                used_model = model
                break
            except (LLMError, StructuredOutputError, OSError, TimeoutError, RuntimeError) as exc:
                last_error = str(exc)[:300]
                logger.warning(
                    "insight_check_model_failed",
                    extra={"provider": str(provider), "model": model, "error": last_error},
                )
                continue
        if assessment is None:
            return [
                {
                    "sentence": s,
                    "status": "error",
                    "phrase": None,
                    "problem": f"verifier unavailable: {last_error or 'no model'}",
                    "provider": None,
                    "model": None,
                }
                for s in sentences
            ]
        by_index = {v.sentence_index: v for v in assessment.verdicts}
        out = []
        for i, sentence in enumerate(sentences):
            verdict = by_index.get(i)
            if verdict is None or verdict.status not in {"supported", "unsupported"}:
                out.append(
                    {
                        "sentence": sentence,
                        "status": "error",
                        "phrase": None,
                        "problem": "verifier skipped this sentence",
                        "provider": used_provider,
                        "model": used_model,
                    }
                )
            else:
                out.append(
                    {
                        "sentence": sentence,
                        "status": verdict.status,
                        "phrase": verdict.unsupported_phrase,
                        "problem": None,
                        "provider": used_provider,
                        "model": used_model,
                    }
                )
        return out
    finally:
        if close and hasattr(local, "aclose"):
            await local.aclose()
