"""Unified model client for the Meta Model API (hosted) and Baseten (open weights).

Both providers are called through an OpenAI-compatible chat-completions interface.
Every call produces a ModelCallRecord (provider, model, task, prompt version, tokens, cost,
latency, status) that is handed to a recorder, so all model usage is observable.

Policy:
- Retry only transient failures (network errors, timeouts, 429, 5xx) with exponential backoff.
- Never retry invalid JSON blindly: one repair attempt that shows the model its validation
  error, then fail loudly with StructuredOutputError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class Provider(StrEnum):
    META = "meta"
    BASETEN = "baseten"


class LLMError(RuntimeError):
    """A model call failed after retries, or with a non-retryable error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class StructuredOutputError(LLMError):
    """The model did not return JSON matching the requested schema."""


@dataclass
class ModelCallRecord:
    provider: str
    model: str
    status: str
    task: str | None = None
    prompt_version: str | None = None
    attempts: int = 1
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    run_id: str | None = None
    step_id: str | None = None
    error: str | None = None


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    attempts: int
    cost_usd: Decimal | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class FunctionCallItem:
    """One function_call item from a Responses API output list."""

    call_id: str
    name: str
    arguments: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class ResponsesResult:
    """Parsed Meta / OpenAI Responses API payload (non-streaming)."""

    id: str
    output: list[dict[str, Any]]
    function_calls: list[FunctionCallItem]
    commentary: str
    tool_choice: Any
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    attempts: int
    cost_usd: Decimal | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


Recorder = Callable[[ModelCallRecord], None]


def _log_recorder(record: ModelCallRecord) -> None:
    logger.info("model_call", extra={"model_call": record.__dict__})


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating a surrounding ```json fence."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        stripped = match.group(1)
    return json.loads(stripped)


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        recorder: Recorder | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds, transport=transport
        )
        self._recorder = recorder or _log_recorder
        self._sleep = sleep
        self._pricing: dict[str, tuple[float, float]] = {
            k: (float(v[0]), float(v[1]))
            for k, v in json.loads(self.settings.model_pricing_json or "{}").items()
        }
        # Sticky tool_choice after first accepted /responses call ("required" or "auto").
        self._responses_tool_choice: Any | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ providers
    def _endpoint(self, provider: Provider) -> tuple[str, str]:
        s = self.settings
        if provider is Provider.META:
            base, key = s.meta_model_api_base_url, s.model_api_key
        else:
            base, key = s.baseten_base_url, s.baseten_api_key
        if not base or not key:
            raise LLMError(f"Provider '{provider}' is not configured (base URL or API key missing)")
        return base.rstrip("/"), key

    def _cost(
        self, model: str, input_tokens: int | None, output_tokens: int | None
    ) -> Decimal | None:
        price = self._pricing.get(model)
        if price is None or input_tokens is None or output_tokens is None:
            return None
        usd = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
        return Decimal(str(round(usd, 6)))

    # ------------------------------------------------------------------ core call
    async def chat(
        self,
        *,
        provider: Provider,
        model: str,
        messages: list[dict[str, Any]],
        task: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResult:
        base, key = self._endpoint(provider)
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format
        if extra_body:
            body.update(extra_body)

        record = ModelCallRecord(
            provider=str(provider),
            model=model,
            status="error",
            task=task,
            prompt_version=prompt_version,
            run_id=run_id,
            step_id=step_id,
        )
        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        last_status: int | None = None

        try:
            while attempts <= self.settings.llm_max_retries:
                attempts += 1
                retry_after: float | None = None
                try:
                    resp = await self._http.post(
                        f"{base}/chat/completions",
                        json=body,
                        headers={"Authorization": f"Bearer {key}"},
                    )
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_error, last_status = f"{type(exc).__name__}: {exc}", None
                else:
                    if resp.status_code < 400:
                        data = resp.json()
                        usage = data.get("usage") or {}
                        content = _message_content(data)
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        in_tok = usage.get("prompt_tokens")
                        out_tok = usage.get("completion_tokens")
                        cost = self._cost(model, in_tok, out_tok)
                        record.status = "success"
                        record.input_tokens, record.output_tokens = in_tok, out_tok
                        record.cost_usd = cost
                        return LLMResult(
                            content=content,
                            provider=str(provider),
                            model=model,
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            latency_ms=latency_ms,
                            attempts=attempts,
                            cost_usd=cost,
                            raw=data,
                        )
                    last_status = resp.status_code
                    last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    if resp.status_code not in RETRYABLE_STATUS:
                        raise LLMError(last_error, status_code=resp.status_code)
                    retry_after = _parse_retry_after(resp.headers.get("retry-after"))

                if attempts > self.settings.llm_max_retries:
                    break
                delay = retry_after if retry_after is not None else _backoff(attempts)
                logger.warning(
                    "model_call_retry",
                    extra={
                        "model": model,
                        "attempt": attempts,
                        "delay_s": delay,
                        "error": last_error,
                    },
                )
                await self._sleep(delay)

            raise LLMError(
                f"Model call failed after {attempts} attempts: {last_error}", last_status
            )
        except LLMError as exc:
            record.error = str(exc)
            raise
        finally:
            record.attempts = attempts
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            self._recorder(record)

    # ------------------------------------------------------------------ responses API
    async def responses(
        self,
        *,
        provider: Provider,
        model: str,
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
        """Call Meta ``/responses`` (stream=false, parallel_tool_calls=false, store=true).

        Tries ``tool_choice="required"`` first (or a sticky previously accepted value), then
        falls back to ``"auto"`` if the API rejects it. Records the choice that succeeded.
        """
        base, key = self._endpoint(provider)
        if tool_choice is not None:
            preferred: Any = tool_choice
        elif self._responses_tool_choice is not None:
            preferred = self._responses_tool_choice
        else:
            preferred = "required"
        choices_to_try: list[Any] = [preferred]
        if preferred != "auto":
            choices_to_try.append("auto")

        record = ModelCallRecord(
            provider=str(provider),
            model=model,
            status="error",
            task=task,
            prompt_version=prompt_version,
            run_id=run_id,
            step_id=step_id,
        )
        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        last_status: int | None = None

        try:
            for choice_idx, choice in enumerate(choices_to_try):
                body: dict[str, Any] = {
                    "model": model,
                    "input": input,
                    "stream": False,
                    "parallel_tool_calls": False,
                    "store": True,
                    "tool_choice": choice,
                }
                if instructions is not None:
                    body["instructions"] = instructions
                if tools is not None:
                    body["tools"] = tools
                if previous_response_id is not None:
                    body["previous_response_id"] = previous_response_id
                if extra_body:
                    body.update(extra_body)

                while attempts <= self.settings.llm_max_retries:
                    attempts += 1
                    retry_after: float | None = None
                    try:
                        resp = await self._http.post(
                            f"{base}/responses",
                            json=body,
                            headers={"Authorization": f"Bearer {key}"},
                        )
                    except (httpx.TransportError, httpx.TimeoutException) as exc:
                        last_error, last_status = f"{type(exc).__name__}: {exc}", None
                    else:
                        if resp.status_code < 400:
                            data = resp.json()
                            usage = data.get("usage") or {}
                            latency_ms = int((time.perf_counter() - started) * 1000)
                            in_tok = usage.get("input_tokens", usage.get("prompt_tokens"))
                            out_tok = usage.get("output_tokens", usage.get("completion_tokens"))
                            cost = self._cost(model, in_tok, out_tok)
                            record.status = "success"
                            record.input_tokens, record.output_tokens = in_tok, out_tok
                            record.cost_usd = cost
                            self._responses_tool_choice = choice
                            output_items = _responses_output_items(data)
                            return ResponsesResult(
                                id=str(data.get("id") or ""),
                                output=output_items,
                                function_calls=_responses_function_calls(output_items),
                                commentary=_responses_commentary(output_items),
                                tool_choice=choice,
                                provider=str(provider),
                                model=model,
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                latency_ms=latency_ms,
                                attempts=attempts,
                                cost_usd=cost,
                                raw=data,
                            )

                        last_status = resp.status_code
                        last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                        # Non-retryable rejection of tool_choice → try next choice.
                        if (
                            resp.status_code not in RETRYABLE_STATUS
                            and choice_idx < len(choices_to_try) - 1
                            and _looks_like_tool_choice_rejection(resp.text)
                        ):
                            logger.warning(
                                "responses_tool_choice_fallback",
                                extra={
                                    "from": choice,
                                    "to": choices_to_try[choice_idx + 1],
                                    "error": last_error,
                                },
                            )
                            break  # next tool_choice
                        if resp.status_code not in RETRYABLE_STATUS:
                            raise LLMError(last_error, status_code=resp.status_code)
                        retry_after = _parse_retry_after(resp.headers.get("retry-after"))

                    if attempts > self.settings.llm_max_retries:
                        raise LLMError(
                            f"Model call failed after {attempts} attempts: {last_error}",
                            last_status,
                        )
                    delay = retry_after if retry_after is not None else _backoff(attempts)
                    logger.warning(
                        "model_call_retry",
                        extra={
                            "model": model,
                            "attempt": attempts,
                            "delay_s": delay,
                            "error": last_error,
                        },
                    )
                    await self._sleep(delay)
                else:
                    continue
                # broke out to try next tool_choice — do not count the failed choice as final
                continue

            raise LLMError(
                f"Model call failed after {attempts} attempts: {last_error}", last_status
            )
        except LLMError as exc:
            record.error = str(exc)
            raise
        finally:
            record.attempts = attempts
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            self._recorder(record)

    # ------------------------------------------------------------------ structured output
    async def structured_json(
        self,
        *,
        provider: Provider,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[T],
        task: str | None = None,
        prompt_version: str | None = None,
        use_json_schema: bool = True,
        **kwargs: Any,
    ) -> tuple[T, LLMResult]:
        """Call a model and validate its JSON output against a Pydantic schema.

        On invalid output, makes exactly one repair attempt that includes the validation error.
        """
        response_format = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": strict_json_schema(schema.model_json_schema()),
                    "strict": True,
                },
            }
            if use_json_schema
            else {"type": "json_object"}
        )
        convo = list(messages)
        last_error = ""
        for attempt in range(2):
            result = await self.chat(
                provider=provider,
                model=model,
                messages=convo,
                task=task,
                prompt_version=prompt_version,
                response_format=response_format,
                **kwargs,
            )
            try:
                return schema.model_validate(extract_json(result.content)), result
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:2000]
                logger.warning(
                    "structured_output_invalid",
                    extra={
                        "model": model,
                        "task": task,
                        "attempt": attempt + 1,
                        "error": last_error,
                    },
                )
                convo = [
                    *messages,
                    {"role": "assistant", "content": result.content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous answer was not valid JSON for the required schema. "
                            f"Validation error:\n{last_error}\n"
                            "Return only corrected JSON. Use null for unknown fields; "
                            "do not invent values."
                        ),
                    },
                ]
        raise StructuredOutputError(f"Invalid structured output from {model}: {last_error}")


def strict_json_schema(schema: Any) -> Any:
    """Make a JSON schema valid for strict structured outputs.

    Strict mode requires every object to declare additionalProperties: false and to list all of
    its properties as required (optional fields stay nullable via anyOf/null). Pydantic emits
    neither by default, and default values are not allowed either.
    """
    if isinstance(schema, list):
        return [strict_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_json_schema(v) for k, v in schema.items() if k != "default"}
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def _message_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc
    if isinstance(content, list):  # some providers return content parts
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    return min(cap, base * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(60.0, max(0.0, float(value)))
    except ValueError:
        return None


def _responses_output_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = data.get("output")
    if isinstance(out, list):
        return [item for item in out if isinstance(item, dict)]
    return []


def _responses_function_calls(items: list[dict[str, Any]]) -> list[FunctionCallItem]:
    calls: list[FunctionCallItem] = []
    for item in items:
        if item.get("type") != "function_call":
            continue
        call_id = str(item.get("call_id") or item.get("id") or "")
        name = str(item.get("name") or "")
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {})
        calls.append(FunctionCallItem(call_id=call_id, name=name, arguments=arguments, raw=item))
    return calls


def _responses_commentary(items: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in items:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks)


def _looks_like_tool_choice_rejection(body: str) -> bool:
    lower = body.lower()
    return "tool_choice" in lower or "tool choice" in lower or "required" in lower
