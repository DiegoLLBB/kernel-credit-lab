import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import LLMClient, LLMError, ModelCallRecord, Provider, StructuredOutputError


class BondTerms(BaseModel):
    coupon: float
    maturity_date: str
    par_call_date: str | None


def make_settings(**overrides) -> Settings:
    base = dict(
        model_api_key="test-key",
        meta_model_api_base_url="https://meta.test/v1",
        baseten_api_key="bt-key",
        baseten_base_url="https://baseten.test/v1",
        llm_max_retries=3,
        model_pricing_json=json.dumps({"spark": [1.0, 4.0]}),
    )
    base.update(overrides)
    return Settings(**base)


def completion(content: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


class Harness:
    """Scripted HTTP responses plus captured requests and model-call records."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.records: list[ModelCallRecord] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self, **settings_overrides) -> LLMClient:
        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        return LLMClient(
            make_settings(**settings_overrides),
            transport=httpx.MockTransport(self.handler),
            recorder=self.records.append,
            sleep=fake_sleep,
        )


async def test_chat_success_records_tokens_cost_and_routes_to_provider():
    h = Harness([httpx.Response(200, json=completion("hello", 1_000_000, 0))])
    async with h.client() as client:
        result = await client.chat(
            provider=Provider.META,
            model="spark",
            messages=[{"role": "user", "content": "hi"}],
            task="smoke",
        )
    assert result.content == "hello"
    assert str(h.requests[0].url) == "https://meta.test/v1/chat/completions"
    assert h.requests[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(h.requests[0].content)
    assert body["temperature"] == 0.0
    rec = h.records[0]
    assert rec.status == "success" and rec.attempts == 1 and rec.task == "smoke"
    assert float(rec.cost_usd) == pytest.approx(1.0)


async def test_baseten_provider_uses_its_own_endpoint_and_key():
    h = Harness([httpx.Response(200, json=completion("ok"))])
    async with h.client() as client:
        await client.chat(
            provider=Provider.BASETEN,
            model="meta-models/muse-spark-open-1026",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert str(h.requests[0].url) == "https://baseten.test/v1/chat/completions"
    assert h.requests[0].headers["authorization"] == "Bearer bt-key"


async def test_retries_transient_errors_then_succeeds():
    h = Harness(
        [
            httpx.Response(503, text="busy"),
            httpx.ConnectError("boom"),
            httpx.Response(429, text="slow down", headers={"retry-after": "2"}),
            httpx.Response(200, json=completion("ok")),
        ]
    )
    async with h.client() as client:
        result = await client.chat(provider=Provider.META, model="spark", messages=[])
    assert result.attempts == 4
    assert len(h.sleeps) == 3 and h.sleeps[2] == 2.0  # Retry-After honoured
    assert h.records[0].status == "success" and h.records[0].attempts == 4


async def test_does_not_retry_client_errors():
    h = Harness([httpx.Response(400, text="bad request")])
    async with h.client() as client:
        with pytest.raises(LLMError) as exc:
            await client.chat(provider=Provider.META, model="spark", messages=[])
    assert exc.value.status_code == 400
    assert len(h.requests) == 1 and h.sleeps == []
    assert h.records[0].status == "error"


async def test_gives_up_after_max_retries():
    h = Harness([httpx.Response(500, text="err")] * 3)
    async with h.client(llm_max_retries=2) as client:
        with pytest.raises(LLMError):
            await client.chat(provider=Provider.META, model="spark", messages=[])
    assert len(h.requests) == 3
    assert h.records[0].attempts == 3


async def test_unconfigured_provider_fails_fast():
    h = Harness([])
    async with h.client(baseten_api_key="") as client:
        with pytest.raises(LLMError, match="not configured"):
            await client.chat(provider=Provider.BASETEN, model="x", messages=[])
    assert h.requests == []


async def test_structured_json_validates_and_accepts_fenced_json():
    payload = {"coupon": 4.75, "maturity_date": "2031-06-15", "par_call_date": "2031-03-15"}
    h = Harness([httpx.Response(200, json=completion(f"```json\n{json.dumps(payload)}\n```"))])
    async with h.client() as client:
        terms, _ = await client.structured_json(
            provider=Provider.META, model="spark", messages=[], schema=BondTerms
        )
    assert terms.coupon == 4.75
    body = json.loads(h.requests[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "BondTerms"


async def test_structured_json_repairs_once_with_validation_error():
    good = {"coupon": 2.25, "maturity_date": "2027-03-01", "par_call_date": None}
    h = Harness(
        [
            httpx.Response(200, json=completion('{"coupon": "two"}')),
            httpx.Response(200, json=completion(json.dumps(good))),
        ]
    )
    async with h.client() as client:
        terms, _ = await client.structured_json(
            provider=Provider.META, model="spark", messages=[], schema=BondTerms
        )
    assert terms.par_call_date is None
    repair_messages = json.loads(h.requests[1].content)["messages"]
    assert "Validation error" in repair_messages[-1]["content"]


async def test_structured_json_fails_loudly_after_repair():
    h = Harness([httpx.Response(200, json=completion("not json"))] * 2)
    async with h.client() as client:
        with pytest.raises(StructuredOutputError):
            await client.structured_json(
                provider=Provider.META, model="spark", messages=[], schema=BondTerms
            )
    assert len(h.requests) == 2
