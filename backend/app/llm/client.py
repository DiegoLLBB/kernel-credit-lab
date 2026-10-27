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
                    "schema": schema.model_json_schema(),
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
