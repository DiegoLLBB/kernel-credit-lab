"""Independent claims check for submit_alert (CONTEXT.md §9.3 step ②)."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.normalize import extract_numbers
from app.config import get_settings
from app.llm.client import LLMClient, LLMError, Provider, StructuredOutputError
from app.llm.recorder import db_recorder
from app.models import Fact
from app.models.trace import AgentRun

logger = logging.getLogger(__name__)

PROMPT_VERSION = "claims_v2"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "claims_v2.md"
FALLBACK_MODEL = "muse-spark-1.3"

# Optional override (agent loop injects its client; tests pass claims_client=…).
_claims_client_override: ContextVar[Any | None] = ContextVar("claims_client_override", default=None)


def use_claims_client(client: Any) -> Token[Any | None]:
    """Bind a claims-check LLM client for the current context (agent loop / tests)."""
    return _claims_client_override.set(client)


def reset_claims_client(token: Token[Any | None]) -> None:
    _claims_client_override.reset(token)


ClaimsStatus = Literal["pass", "fail", "error"]
FindingStatus = Literal["supported", "overstated", "unsupported"]


class ItemVerdictModel(BaseModel):
    """Status for the summary or one finding."""

    status: FindingStatus
    unsupported_phrase: str | None = Field(
        description="Short unsupported phrase when status is overstated/unsupported; else null"
    )


class FindingVerdictModel(ItemVerdictModel):
    """One finding assessment from the verifier model."""

    finding_index: int = Field(description="0-based index into the findings list")
    item_key: str | None = Field(description="Checklist item key when provided")


class ClaimsAssessmentModel(BaseModel):
    """Strict structured output for the claims check."""

    summary: ItemVerdictModel = Field(
        description="Verdict for the alert summary against all cited quotes and metrics"
    )
    findings: list[FindingVerdictModel]


@dataclass
class ClaimsResult:
    """Outcome of the independent claims check."""

    status: ClaimsStatus
    problems: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None

    def to_audit(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "problems": list(self.problems),
            "summary": self.summary,
            "findings": list(self.findings),
            "provider": self.provider,
            "model": self.model,
        }


def load_claims_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _metric_number_strings(metrics_outputs: list[Any] | None) -> list[str]:
    found: set[Decimal] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, Decimal):
            found.add(obj)
        else:
            try:
                if isinstance(obj, str) and obj.replace(".", "", 1).replace("-", "", 1).isdigit():
                    found.add(Decimal(obj))
                elif isinstance(obj, (int, float)):
                    found.add(Decimal(str(obj)))
            except Exception:
                return

    for block in metrics_outputs or []:
        walk(block)
    return [str(n) for n in sorted(found, key=lambda d: (abs(d), str(d)))]


def _build_payload(
    *,
    summary: str,
    findings: Sequence[dict[str, Any]],
    facts_by_id: dict[str, Fact],
    metrics_outputs: list[Any] | None,
) -> dict[str, Any]:
    metric_numbers = _metric_number_strings(metrics_outputs)
    finding_payloads: list[dict[str, Any]] = []
    all_quotes: list[str] = []
    seen_quotes: set[str] = set()
    for finding in findings:
        citations = finding.get("citations") or finding.get("fact_ids") or []
        quotes: list[str] = []
        for cid in citations:
            fact = facts_by_id.get(str(cid))
            if fact is None:
                continue
            quotes.append(fact.exact_quote)
            if fact.exact_quote not in seen_quotes:
                seen_quotes.add(fact.exact_quote)
                all_quotes.append(fact.exact_quote)
        statement = str(finding.get("statement") or finding.get("text") or "")
        stated = extract_numbers(statement)
        cited_metrics = [n for n in metric_numbers if Decimal(n) in stated]
        finding_payloads.append(
            {
                "item_key": finding.get("item_key"),
                "statement": statement,
                "quotes": quotes,
                "metric_numbers": cited_metrics or metric_numbers,
            }
        )
    return {
        "summary": {
            "text": summary,
            "quotes": all_quotes,
            "metric_numbers": metric_numbers,
        },
        "metric_numbers": metric_numbers,
        "findings": finding_payloads,
    }


def _provider_model_attempts(settings: Any) -> list[tuple[Provider, str]]:
    """Baseten OPEN_MODEL first when keyed; Meta muse-spark as fallback."""
    attempts: list[tuple[Provider, str]] = []
    if settings.baseten_api_key:
        attempts.append(
            (Provider.BASETEN, settings.open_model or "meta-models/muse-spark-open-1026")
        )
    attempts.append((Provider.META, settings.default_model or FALLBACK_MODEL))
    return attempts


def _problems_from_assessment(
    assessment: ClaimsAssessmentModel, findings: Sequence[dict[str, Any]]
) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    problems: list[str] = []
    summary_detail = {
        "status": assessment.summary.status,
        "unsupported_phrase": assessment.summary.unsupported_phrase,
    }
    if assessment.summary.status in {"overstated", "unsupported"}:
        phrase = (assessment.summary.unsupported_phrase or "").strip() or "(no phrase given)"
        problems.append(f"claims check {assessment.summary.status}: summary: {phrase}")

    detail: list[dict[str, Any]] = []
    for verdict in assessment.findings:
        row = {
            "finding_index": verdict.finding_index,
            "item_key": verdict.item_key,
            "status": verdict.status,
            "unsupported_phrase": verdict.unsupported_phrase,
        }
        detail.append(row)
        if verdict.status in {"overstated", "unsupported"}:
            phrase = (verdict.unsupported_phrase or "").strip() or "(no phrase given)"
            key = verdict.item_key
            if key is None and 0 <= verdict.finding_index < len(findings):
                key = findings[verdict.finding_index].get("item_key")
            label = f"finding {key!r}" if key else f"finding[{verdict.finding_index}]"
            problems.append(f"claims check {verdict.status}: {label}: {phrase}")
    return problems, summary_detail, detail


async def claims_check(
    session: Session,
    run: AgentRun,
    summary: str,
    findings: list[dict[str, Any]],
    *,
    client: Any | None = None,
    facts_by_id: dict[str, Fact] | None = None,
) -> ClaimsResult:
    """Await the independent claims check on the caller's event loop; fail closed."""
    del session  # ModelCall persistence uses db_recorder's own session
    if facts_by_id is None:
        facts_by_id = {}

    local = client if client is not None else _claims_client_override.get()
    close = False
    if local is None:
        local = LLMClient(recorder=db_recorder)
        close = True

    settings = getattr(local, "settings", None) or get_settings()
    payload = _build_payload(
        summary=summary,
        findings=findings,
        facts_by_id=facts_by_id,
        metrics_outputs=list(run.metrics_outputs or []),
    )
    system = load_claims_prompt()
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Assess the summary and each finding. Input JSON follows.\n\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]

    try:
        last_error: str | None = None
        assessment: ClaimsAssessmentModel | None = None
        used_provider: str | None = None
        used_model: str | None = None
        for provider, model in _provider_model_attempts(settings):
            try:
                assessment, _llm = await local.structured_json(
                    provider=provider,
                    model=model,
                    messages=messages,
                    schema=ClaimsAssessmentModel,
                    task="claims_check",
                    prompt_version=PROMPT_VERSION,
                    temperature=0.0,
                    run_id=run.id,
                )
                used_provider = str(provider)
                used_model = model
                break
            except (LLMError, StructuredOutputError, OSError, TimeoutError, RuntimeError) as exc:
                last_error = str(exc)[:300]
                logger.warning(
                    "claims_check_model_failed",
                    extra={"provider": str(provider), "model": model, "error": last_error},
                )
                continue

        if assessment is None:
            return ClaimsResult(
                status="error",
                problems=["claims check unavailable", last_error or "no model available"],
            )

        problems, summary_detail, detail = _problems_from_assessment(assessment, findings)
        if problems:
            return ClaimsResult(
                status="fail",
                problems=problems,
                summary=summary_detail,
                findings=detail,
                provider=used_provider,
                model=used_model,
            )
        return ClaimsResult(
            status="pass",
            problems=[],
            summary=summary_detail,
            findings=detail,
            provider=used_provider,
            model=used_model,
        )
    except Exception as exc:  # fail closed
        return ClaimsResult(
            status="error",
            problems=["claims check unavailable", str(exc)[:300]],
        )
    finally:
        if close and hasattr(local, "aclose"):
            await local.aclose()
