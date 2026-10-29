"""Scripted FakeModel for offline agent-loop tests (Responses API shape)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.llm.client import (
    FunctionCallItem,
    ModelCallRecord,
    Provider,
    ResponsesResult,
    _responses_commentary,
    _responses_function_calls,
    _responses_output_items,
)

ScriptEntry = dict[str, Any] | Callable[["FakeContext"], dict[str, Any]]


@dataclass
class FakeContext:
    """Prior tool results for dynamic script turns."""

    tool_results: list[dict[str, Any]] = field(default_factory=list)
    call_count: int = 0

    def fact_ids(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for result in self.tool_results:
            if result.get("ok") and result.get("fact_id") and result.get("item_key"):
                out[str(result["item_key"])] = str(result["fact_id"])
            # record_fact returns fact_id; item_key is in arguments — also accept nested
            if result.get("ok") and "fact_id" in result:
                # try to recover from result payload
                key = result.get("item_key")
                if key:
                    out[str(key)] = str(result["fact_id"])
        return out


def function_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str | None = None,
    commentary: str = "",
    response_id: str | None = None,
    reasoning: str | None = "planning next tool",
) -> dict[str, Any]:
    """Build a raw Responses-shaped payload with reasoning → commentary → function_call."""
    cid = call_id or f"call_{uuid.uuid4().hex[:12]}"
    rid = response_id or f"resp_{uuid.uuid4().hex[:12]}"
    output: list[dict[str, Any]] = []
    if reasoning is not None:
        output.append(
            {
                "type": "reasoning",
                "id": f"rs_{uuid.uuid4().hex[:8]}",
                "summary": [{"type": "summary_text", "text": reasoning}],
            }
        )
    if commentary:
        output.append(
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": commentary}],
            }
        )
    output.append(
        {
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:8]}",
            "call_id": cid,
            "name": name,
            "arguments": json.dumps(arguments or {}),
        }
    )
    return {
        "id": rid,
        "object": "response",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def plain_text(text: str, *, response_id: str | None = None) -> dict[str, Any]:
    """Responses payload with commentary only (no function_call)."""
    rid = response_id or f"resp_{uuid.uuid4().hex[:12]}"
    return {
        "id": rid,
        "object": "response",
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "thinking"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 5, "output_tokens": 8},
    }


def multi_call(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    commentary: str = "calling tools",
    response_id: str | None = None,
) -> dict[str, Any]:
    """One response containing several function_call items (loop should run only the first)."""
    rid = response_id or f"resp_{uuid.uuid4().hex[:12]}"
    output: list[dict[str, Any]] = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "multi"}]},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": commentary}],
        },
    ]
    for name, args in calls:
        output.append(
            {
                "type": "function_call",
                "call_id": f"call_{uuid.uuid4().hex[:12]}",
                "name": name,
                "arguments": json.dumps(args),
            }
        )
    return {
        "id": rid,
        "object": "response",
        "output": output,
        "usage": {"input_tokens": 12, "output_tokens": 30},
    }


@dataclass
class FakeClaimsClient:
    """Scripted ``structured_json`` client for the independent claims check (no network)."""

    script: list[Any]
    model: str = "fake-claims"
    settings: Any = None
    records: list[ModelCallRecord] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def __post_init__(self) -> None:
        if self.settings is None:
            from app.config import Settings

            # No API keys — claims_check will still prefer META fallback model id.
            self.settings = Settings(model_api_key="fake", meta_model_api_base_url="https://fake")

    async def structured_json(
        self,
        *,
        provider: Provider = Provider.META,
        model: str | None = None,
        messages: list[dict[str, Any]],
        schema: Any,
        task: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
        run_id: str | None = None,
        step_id: str | None = None,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        from app.llm.client import LLMResult

        self.requests.append(
            {
                "provider": str(provider),
                "model": model or self.model,
                "messages": messages,
                "task": task,
                "prompt_version": prompt_version,
                "temperature": temperature,
                "run_id": run_id,
            }
        )
        if self._index >= len(self.script):
            raise RuntimeError(f"FakeClaimsClient script exhausted at index {self._index}")
        entry = self.script[self._index]
        self._index += 1
        if isinstance(entry, Exception):
            self.records.append(
                ModelCallRecord(
                    provider=str(provider),
                    model=model or self.model,
                    status="error",
                    task=task,
                    prompt_version=prompt_version,
                    run_id=run_id,
                    step_id=step_id,
                    error=str(entry),
                )
            )
            raise entry
        payload = entry(messages) if callable(entry) else entry
        parsed = schema.model_validate(payload)
        self.records.append(
            ModelCallRecord(
                provider=str(provider),
                model=model or self.model,
                status="success",
                task=task,
                prompt_version=prompt_version,
                attempts=1,
                input_tokens=8,
                output_tokens=12,
                cost_usd=Decimal("0"),
                latency_ms=1,
                run_id=run_id,
                step_id=step_id,
            )
        )
        result = LLMResult(
            content=json.dumps(payload),
            provider=str(provider),
            model=model or self.model,
            input_tokens=8,
            output_tokens=12,
            latency_ms=1,
            attempts=1,
            cost_usd=Decimal("0"),
            raw={"scripted": True},
        )
        return parsed, result

    async def aclose(self) -> None:
        return None


class FakeModel:
    """Duck-types ``LLMClient.responses``; replays scripted Responses payloads. No network."""

    def __init__(
        self,
        script: Sequence[ScriptEntry],
        *,
        model: str = "fake-spark",
        tool_choice: str = "required",
        raise_on_previous_response_id: bool = False,
    ) -> None:
        self.script = list(script)
        self.model = model
        self.tool_choice = tool_choice
        self.raise_on_previous_response_id = raise_on_previous_response_id
        self.context = FakeContext()
        self.records: list[ModelCallRecord] = []
        self.requests: list[dict[str, Any]] = []
        self._index = 0
        self._expired_once = False
        from app.config import Settings

        # So claims_check can resolve provider/model without a live API.
        self.settings = Settings(model_api_key="fake", meta_model_api_base_url="https://fake")

    def note_tool_result(self, result: dict[str, Any], *, item_key: str | None = None) -> None:
        payload = dict(result)
        if item_key and "item_key" not in payload:
            payload["item_key"] = item_key
        self.context.tool_results.append(payload)

    async def structured_json(
        self,
        *,
        provider: Provider = Provider.META,
        model: str | None = None,
        messages: list[dict[str, Any]],
        schema: Any,
        task: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
        run_id: str | None = None,
        step_id: str | None = None,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        """Default claims-check behaviour for loop tests: every finding is supported."""
        from app.llm.client import LLMResult

        findings_n = 0
        item_keys: list[str | None] = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content") or "")
            marker = "Input JSON follows."
            if marker in text:
                raw = text.split(marker, 1)[1].strip()
                try:
                    payload = json.loads(raw)
                    items = payload.get("findings") or []
                    findings_n = len(items)
                    item_keys = [it.get("item_key") for it in items]
                except json.JSONDecodeError:
                    findings_n = 0
        data = {
            "summary": {"status": "supported", "unsupported_phrase": None},
            "findings": [
                {
                    "finding_index": i,
                    "item_key": item_keys[i] if i < len(item_keys) else None,
                    "status": "supported",
                    "unsupported_phrase": None,
                }
                for i in range(findings_n)
            ],
        }
        parsed = schema.model_validate(data)
        self.records.append(
            ModelCallRecord(
                provider=str(provider),
                model=model or self.model,
                status="success",
                task=task,
                prompt_version=prompt_version,
                attempts=1,
                input_tokens=8,
                output_tokens=12,
                cost_usd=Decimal("0"),
                latency_ms=1,
                run_id=run_id,
                step_id=step_id,
            )
        )
        return parsed, LLMResult(
            content=json.dumps(data),
            provider=str(provider),
            model=model or self.model,
            input_tokens=8,
            output_tokens=12,
            latency_ms=1,
            attempts=1,
            cost_usd=Decimal("0"),
            raw={"scripted": True},
        )

    async def responses(
        self,
        *,
        provider: Provider = Provider.META,
        model: str | None = None,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        previous_response_id: str | None = None,
        tool_choice: Any | None = None,
        task: str | None = None,
        prompt_version: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ResponsesResult:
        from app.llm.client import LLMError

        self.requests.append(
            {
                "provider": str(provider),
                "model": model or self.model,
                "input": input,
                "instructions": instructions,
                "previous_response_id": previous_response_id,
                "tool_choice": tool_choice if tool_choice is not None else self.tool_choice,
                "tools": tools,
                "extra_body": extra_body,
                "run_id": run_id,
                "step_id": step_id,
            }
        )
        if self.raise_on_previous_response_id and previous_response_id and not self._expired_once:
            self._expired_once = True
            rec = ModelCallRecord(
                provider=str(provider),
                model=model or self.model,
                status="error",
                task=task,
                prompt_version=prompt_version,
                run_id=run_id,
                step_id=step_id,
                error="HTTP 400: previous_response_id expired or not stored",
            )
            self.records.append(rec)
            raise LLMError("HTTP 400: previous_response_id expired or not stored", status_code=400)

        if self._index >= len(self.script):
            raise RuntimeError(f"FakeModel script exhausted at index {self._index}")

        entry = self.script[self._index]
        self._index += 1
        self.context.call_count += 1
        raw = entry(self.context) if callable(entry) else entry
        started = time.perf_counter()
        output_items = _responses_output_items(raw)
        usage = raw.get("usage") or {}
        in_tok = usage.get("input_tokens", 10)
        out_tok = usage.get("output_tokens", 20)
        cost = Decimal("0.000001")
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        choice = tool_choice if tool_choice is not None else self.tool_choice
        result = ResponsesResult(
            id=str(raw.get("id") or f"resp_{self._index}"),
            output=output_items,
            function_calls=_responses_function_calls(output_items),
            commentary=_responses_commentary(output_items),
            tool_choice=choice,
            provider=str(provider),
            model=model or self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            attempts=1,
            cost_usd=cost,
            raw=raw,
        )
        self.records.append(
            ModelCallRecord(
                provider=str(provider),
                model=result.model,
                status="success",
                task=task,
                prompt_version=prompt_version,
                attempts=1,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
                latency_ms=latency_ms,
                run_id=run_id,
                step_id=step_id,
            )
        )
        return result


# Re-export for tests that build FunctionCallItem by hand
__all__ = [
    "FakeClaimsClient",
    "FakeContext",
    "FakeModel",
    "FunctionCallItem",
    "function_call",
    "multi_call",
    "plain_text",
]
