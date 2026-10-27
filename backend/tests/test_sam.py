import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError
from app.llm.sam import parse_segmentation, segment_image

SAMPLE = (
    "<0f>0<|box;x1=117;y1=585;x2=1341;y2=805;w=1446;h=1800;c=0.94|>"
    "<|mask;x=0;y=0;data=55,306,~abc|>,"
    "3<|box;x1=10;y1=20;x2=30;y2=40;w=1446;h=1800|><|mask;x=0;y=0;data=2,2,!xy|>"
)


def test_parse_segmentation_reads_boxes_ids_and_confidence():
    regions = parse_segmentation(SAMPLE)
    assert [r.object_id for r in regions] == [0, 3]
    first = regions[0]
    assert (first.x1, first.y1, first.x2, first.y2) == (117, 585, 1341, 805)
    assert first.confidence == pytest.approx(0.94)
    assert regions[1].confidence is None
    assert first.normalized[2] == pytest.approx(1341 / 1446)


def test_parse_segmentation_handles_no_matches():
    assert parse_segmentation("<0f>") == []
    assert parse_segmentation("") == []


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def _settings() -> Settings:
    return Settings(
        model_api_key="k",
        meta_model_api_base_url="https://meta.test/v1",
        vision_segmentation_model="sam-3.1",
    )


async def test_segment_image_posts_responses_request_and_parses_stream():
    seen: list[httpx.Request] = []
    records = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _sse(
            {"type": "response.output_text.delta", "delta": SAMPLE[:40]},
            {"type": "response.output_text.delta", "delta": SAMPLE[40:]},
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = LLMClient(_settings(), transport=httpx.MockTransport(handler), recorder=records.append)
    regions, raw = await segment_image(client, b"\x89PNG", "table")
    await client.aclose()

    assert str(seen[0].url) == "https://meta.test/v1/responses"
    body = json.loads(seen[0].content)
    content = body["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "table"}
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert body["metadata"] == {"include_confidence": "true"}
    assert len(regions) == 2 and raw == SAMPLE
    assert records[0].status == "success" and records[0].task == "segment"


async def test_segment_image_raises_on_http_error_and_records_it():
    records = []
    client = LLMClient(
        _settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="bad prompt")),
        recorder=records.append,
    )
    with pytest.raises(LLMError):
        await segment_image(client, b"x", "table")
    await client.aclose()
    assert records[0].status == "error"
