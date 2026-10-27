"""SAM 3.1 segmentation client (Meta Model API, /v1/responses).

SAM returns segmentation as special tokens in a text output, one line per frame:
    <0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=0;y=0;data=H,W,~payload|>,1<|box;...|>...

Kernel only needs the bounding boxes (to crop table regions for extraction and to highlight
them in the UI), so masks are kept as raw tokens and not decoded.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.llm.client import LLMClient, LLMError, ModelCallRecord, Provider

_RECORD_RE = re.compile(r"(?:^|,)\s*(\d+)\s*<\|box;([^|]*)\|>(?:<\|mask;([^|]*)\|>)?")


@dataclass(frozen=True)
class Region:
    object_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    frame_width: float
    frame_height: float
    confidence: float | None
    mask_token: str | None = None

    @property
    def normalized(self) -> tuple[float, float, float, float]:
        """Box as fractions of the frame (0-1), independent of image resolution."""
        return (
            self.x1 / self.frame_width,
            self.y1 / self.frame_height,
            self.x2 / self.frame_width,
            self.y2 / self.frame_height,
        )


def _kv(token: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in token.split(";") if "=" in part)


def parse_segmentation(text: str) -> list[Region]:
    """Parse SAM output text into regions (frame 0 only; Kernel segments single images)."""
    regions: list[Region] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("<0f>"):
            continue
        for match in _RECORD_RE.finditer(line[len("<0f>") :]):
            obj_id, box, mask = match.groups()
            b = _kv(box)
            regions.append(
                Region(
                    object_id=int(obj_id),
                    x1=float(b["x1"]),
                    y1=float(b["y1"]),
                    x2=float(b["x2"]),
                    y2=float(b["y2"]),
                    frame_width=float(b["w"]),
                    frame_height=float(b["h"]),
                    confidence=float(b["c"]) if "c" in b else None,
                    mask_token=mask,
                )
            )
    return regions


def image_data_url(image: bytes, filename: str = "page.png") -> str:
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def _collect_output_text(sse_body: str) -> str:
    """Assemble output text from a Responses API event stream."""
    deltas: list[str] = []
    final: str | None = None
    for line in sse_body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        event = json.loads(payload)
        etype = event.get("type", "")
        if etype == "response.output_text.delta":
            deltas.append(event.get("delta", ""))
        elif etype == "response.output_text.done":
            final = event.get("text", final)
        elif etype in ("response.failed", "error"):
            raise LLMError(f"SAM stream error: {json.dumps(event)[:500]}")
    return final if final is not None else "".join(deltas)


async def segment_image(
    client: LLMClient,
    image: bytes,
    prompt: str,
    *,
    model: str | None = None,
    filename: str = "page.png",
    run_id: str | None = None,
    step_id: str | None = None,
) -> tuple[list[Region], str]:
    """Segment every instance of `prompt` (a short noun phrase, e.g. "table") in an image.

    Returns the parsed regions and the raw SAM output text.
    """
    settings = client.settings
    model = model or settings.vision_segmentation_model
    base, key = client._endpoint(Provider.META)
    body: dict[str, Any] = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url(image, filename)},
                ],
            }
        ],
        "stream": True,
        "metadata": {"include_confidence": "true"},
    }
    record = ModelCallRecord(
        provider=str(Provider.META),
        model=model,
        status="error",
        task="segment",
        prompt_version=f"sam:{prompt}",
        run_id=run_id,
        step_id=step_id,
    )
    started = time.perf_counter()
    try:
        resp = await client._http.post(
            f"{base}/responses",
            json=body,
            headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"},
        )
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}", resp.status_code)
        text = _collect_output_text(resp.text)
        regions = parse_segmentation(text)
        record.status = "success"
        return regions, text
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        raise LLMError(record.error) from exc
    except LLMError as exc:
        record.error = str(exc)
        raise
    finally:
        record.latency_ms = int((time.perf_counter() - started) * 1000)
        client._recorder(record)
