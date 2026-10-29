import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import LLMClient, LLMError, ModelCallRecord, Provider, StructuredOutputError
from app.llm.client import strict_json_schema


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
    sent_schema = body["response_format"]["json_schema"]["schema"]
    assert sent_schema["additionalProperties"] is False
    assert set(sent_schema["required"]) == {"coupon", "maturity_date", "par_call_date"}


def test_strict_json_schema_handles_nested_models_and_defaults():
    class Covenant(BaseModel):
        type: str
        source_page: int | None = None

    class Bond(BaseModel):
        name: str
        covenants: list[Covenant] = []

    schema = strict_json_schema(Bond.model_json_schema())
    nested = schema["$defs"]["Covenant"]
    assert schema["additionalProperties"] is False and nested["additionalProperties"] is False
    assert nested["required"] == ["type", "source_page"]
    assert "default" not in schema["properties"]["covenants"]


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


def _responses_payload(
    *,
    response_id: str = "resp_1",
    commentary: str = "next",
    name: str = "list_documents",
    arguments: str = "{}",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> dict:
    return {
        "id": response_id,
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": commentary}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": name,
                "arguments": arguments,
            },
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


async def test_responses_success_records_tokens_cost_and_flags():
    h = Harness([httpx.Response(200, json=_responses_payload())])
    async with h.client() as client:
        result = await client.responses(
            provider=Provider.META,
            model="spark",
            input="hello",
            tools=[{"type": "function", "name": "list_documents"}],
            instructions="sys",
            task="event_loop",
            prompt_version="event_v1",
            run_id="run-1",
        )
    assert str(h.requests[0].url) == "https://meta.test/v1/responses"
    body = json.loads(h.requests[0].content)
    assert body["stream"] is False
    assert body["parallel_tool_calls"] is False
    assert body["store"] is False
    assert "previous_response_id" not in body
    assert body["tool_choice"] == "required"
    assert result.id == "resp_1"
    assert result.commentary == "next"
    assert len(result.function_calls) == 1
    assert result.function_calls[0].name == "list_documents"
    assert result.tool_choice == "required"
    rec = h.records[0]
    assert rec.status == "success" and rec.run_id == "run-1"
    assert float(rec.cost_usd) == pytest.approx(0.0003)  # 100*1 + 50*4 / 1e6


async def test_responses_falls_back_to_auto_tool_choice():
    h = Harness(
        [
            httpx.Response(400, text='{"error":"tool_choice required is not supported"}'),
            httpx.Response(200, json=_responses_payload()),
            httpx.Response(200, json=_responses_payload(response_id="resp_2")),
        ]
    )
    async with h.client() as client:
        result = await client.responses(provider=Provider.META, model="spark", input="hi", tools=[])
        assert len(h.requests) == 2
        assert json.loads(h.requests[0].content)["tool_choice"] == "required"
        assert json.loads(h.requests[1].content)["tool_choice"] == "auto"
        assert result.tool_choice == "auto"
        # Sticky: next call uses auto without retrying required
        result2 = await client.responses(
            provider=Provider.META, model="spark", input="again", tools=[]
        )
        assert json.loads(h.requests[2].content)["tool_choice"] == "auto"
        assert result2.tool_choice == "auto"


async def test_responses_honours_retry_after():
    h = Harness(
        [
            httpx.Response(429, text="slow", headers={"retry-after": "1.5"}),
            httpx.Response(200, json=_responses_payload()),
        ]
    )
    async with h.client() as client:
        await client.responses(provider=Provider.META, model="spark", input="x")
    assert h.sleeps == [1.5]
