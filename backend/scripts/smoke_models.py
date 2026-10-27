"""Smoke-test every configured model provider.

Usage (from backend/, with .env filled in):
    python -m scripts.smoke_models                 # list models + ping all configured models
    python -m scripts.smoke_models --image page.png  # also test the vision model on a page

What it checks:
  1. Which model IDs the Meta Model API actually exposes (GET /models).
  2. A plain chat call per configured model (latency, tokens).
  3. Structured JSON output against a small bond-terms schema (does json_schema work?).
  4. Optionally, an image request to VISION_SEGMENTATION_MODEL. SAM 3.1's request/response
     shape must be confirmed against the official docs; the raw response is printed so you
     can adapt the client.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.llm import LLMClient, LLMError, ModelCallRecord, Provider

SAMPLE = (
    "Example Utility Holdings Inc. issued $600,000,000 aggregate principal amount of 4.750% "
    "Senior Notes due June 15, 2031. Prior to March 15, 2031 (the Par Call Date) the notes are "
    "redeemable at a make-whole price (Treasury + 25 bps); on or after the Par Call Date, at par."
)


class BondTerms(BaseModel):
    coupon: float
    maturity_date: str
    par_call_date: str | None
    make_whole_spread_bps: int | None
    outstanding_amount_usd: int | None


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def fail(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")


async def list_meta_models() -> None:
    s = get_settings()
    print("\n[1] Meta Model API — available models")
    if not (s.model_api_key and s.meta_model_api_base_url):
        fail("MODEL_API_KEY / META_MODEL_API_BASE_URL not set")
        return
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            resp = await http.get(
                f"{s.meta_model_api_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {s.model_api_key}"},
            )
        except httpx.HTTPError as exc:
            fail(f"request failed: {exc}")
            return
    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return
    data = resp.json()
    ids = [m.get("id") for m in data.get("data", data if isinstance(data, list) else [])]
    ok(f"{len(ids)} models")
    for model_id in ids:
        print(f"      - {model_id}")


async def ping(client: LLMClient, provider: Provider, model: str, label: str) -> None:
    if not model:
        fail(f"{label}: no model configured")
        return
    try:
        result = await client.chat(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            task="smoke_ping",
            max_tokens=2000,
        )
        ok(
            f"{label} ({model}): {result.content.strip()[:60]!r} "
            f"[{result.latency_ms} ms, {result.input_tokens}/{result.output_tokens} tok]"
        )
    except LLMError as exc:
        fail(f"{label} ({model}): {exc}")
        return

    for use_schema in (True, False):
        mode = "json_schema" if use_schema else "json_object"
        try:
            terms, result = await client.structured_json(
                provider=provider,
                model=model,
                schema=BondTerms,
                use_json_schema=use_schema,
                task="smoke_extract",
                prompt_version="smoke_v1",
                messages=[
                    {
                        "role": "system",
                        "content": "Extract bond terms as JSON matching the schema. "
                        "Dates as YYYY-MM-DD. Use null when a value is not stated.",
                    },
                    {"role": "user", "content": SAMPLE},
                ],
                max_tokens=4000,
            )
            ok(f"{label} structured output via {mode}: {terms.model_dump()}")
            return
        except LLMError as exc:
            fail(f"{label} structured output via {mode}: {exc}")


async def vision(client: LLMClient, image: Path) -> None:
    s = get_settings()
    model = s.vision_segmentation_model
    print(f"\n[3] Vision — {image.name} -> {model or '(VISION_SEGMENTATION_MODEL unset)'}")
    if not model:
        return
    mime = mimetypes.guess_type(image.name)[0] or "image/png"
    data_uri = f"data:{mime};base64,{base64.b64encode(image.read_bytes()).decode()}"
    try:
        result = await client.chat(
            provider=Provider.META,
            model=model,
            task="smoke_vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Segment: table"},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        )
        ok(f"responded in {result.latency_ms} ms; raw response (truncated):")
        print(json.dumps(result.raw, indent=2)[:3000])
    except LLMError as exc:
        fail(f"{exc}\n      SAM may use a dedicated endpoint; check the official model docs.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, help="prospectus page image (PNG/JPG) for SAM 3.1")
    args = parser.parse_args()
    s = get_settings()
    records: list[ModelCallRecord] = []

    await list_meta_models()
    async with LLMClient(s, recorder=records.append) as client:
        print("\n[2] Chat + structured output")
        await ping(client, Provider.META, s.default_model, "Muse Spark (DEFAULT_MODEL)")
        await ping(
            client, Provider.META, s.deep_research_model, "Muse Wonder (DEEP_RESEARCH_MODEL)"
        )
        if s.baseten_api_key:
            await ping(client, Provider.BASETEN, s.open_model, "Muse Spark Open (Baseten)")
        else:
            fail("Muse Spark Open: BASETEN_API_KEY not set (skip until credits arrive)")
        if args.image:
            await vision(client, args.image)

    failed = sum(r.status != "success" for r in records)
    print(f"\n{len(records)} model calls, {failed} failed.")
    return 1 if failed and not any(r.status == "success" for r in records) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
