"""Probe Meta Model API /responses for function tools (OpenAI Responses-style).

Usage (from backend/, with .env filled in):
    python -m scripts.probe_tools

Sends one turn that must call get_weather(city), prints the raw ``output`` items,
then submits a function_call_output follow-up and prints the final answer.
Reports which request fields the API accepted and what the response contains.
Does not build the agent loop — this is a wire probe only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from app.config import get_settings

MODEL = "muse-spark-1.3"
USER_PROMPT = "What's the weather in Madrid?"

GET_WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
}

FAKE_WEATHER = json.dumps(
    {"city": "Madrid", "temp_c": 22, "conditions": "clear", "source": "probe"}
)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _print_json(label: str, payload: Any) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _post(url: str, api_key: str, body: dict[str, Any]) -> httpx.Response:
    with httpx.Client(timeout=120.0) as http:
        return http.post(url, headers=_headers(api_key), json=body)


def _try_first_turn(
    url: str, api_key: str
) -> tuple[dict[str, Any], httpx.Response, dict[str, Any]]:
    """Try tool_choice variants until the API accepts the request (or all fail)."""
    base_body: dict[str, Any] = {
        "model": MODEL,
        "input": USER_PROMPT,
        "tools": [GET_WEATHER_TOOL],
        "stream": False,
    }
    # Prefer OpenAI Responses "required"; fall back to forced function / auto.
    choices: list[Any] = [
        "required",
        {"type": "function", "name": "get_weather"},
        "auto",
    ]
    last_resp: httpx.Response | None = None
    last_body: dict[str, Any] = base_body
    for choice in choices:
        body = {**base_body, "tool_choice": choice}
        print(f"\n--- POST {url} (tool_choice={choice!r}) ---")
        resp = _post(url, api_key, body)
        last_resp = resp
        last_body = body
        print(f"HTTP {resp.status_code}")
        if resp.status_code < 400:
            return body, resp, {"accepted_tool_choice": choice}
        print("rejected:")
        print(resp.text)
    assert last_resp is not None
    return last_body, last_resp, {"accepted_tool_choice": None}


def _output_items(data: dict[str, Any]) -> list[Any]:
    out = data.get("output")
    if isinstance(out, list):
        return out
    return []


def _find_function_call(items: list[Any]) -> dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return item
    return None


def _final_text(items: list[Any]) -> str | None:
    chunks: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if text:
                    chunks.append(str(text))
    return "\n".join(chunks) if chunks else None


def main() -> int:
    settings = get_settings()
    base = (settings.meta_model_api_base_url or "").rstrip("/")
    key = settings.model_api_key or ""
    if not base or not key:
        print("MODEL_API_KEY / META_MODEL_API_BASE_URL not set", file=sys.stderr)
        return 2

    url = f"{base}/responses"
    print(f"Probe: {url} model={MODEL}")

    request_body, resp, meta = _try_first_turn(url, key)
    _print_json("request body (first turn)", request_body)

    if resp.status_code >= 400:
        print("\n=== API REJECTED TOOLS (verbatim) ===")
        print(resp.text)
        print("\nAccepted fields: none for a successful tool call (see error above).")
        return 1

    try:
        data = resp.json()
    except json.JSONDecodeError:
        print("\n=== non-JSON response (verbatim) ===")
        print(resp.text)
        return 1

    _print_json("raw response (first turn)", data)
    items = _output_items(data)
    _print_json("output items (first turn)", items)

    print("\n=== response top-level keys ===")
    print(sorted(data.keys()) if isinstance(data, dict) else type(data))

    call = _find_function_call(items)
    if call is None:
        print("\nNo function_call in output; cannot send function_call_output follow-up.")
        print("Final text (if any):", _final_text(items))
        return 1

    call_id = call.get("call_id") or call.get("id")
    print(f"\nfunction_call call_id={call_id!r} name={call.get('name')!r}")
    print(f"arguments={call.get('arguments')!r}")

    # Follow-up: prior function_call + function_call_output (OpenAI Responses pattern).
    follow_input: list[dict[str, Any]] = [
        {"type": "message", "role": "user", "content": USER_PROMPT},
        {
            "type": "function_call",
            "call_id": call.get("call_id"),
            "name": call.get("name"),
            "arguments": call.get("arguments"),
        },
        {
            "type": "function_call_output",
            "call_id": call.get("call_id"),
            "output": FAKE_WEATHER,
        },
    ]
    # Drop None values from the echoed function_call.
    follow_input[1] = {k: v for k, v in follow_input[1].items() if v is not None}

    follow_body: dict[str, Any] = {
        "model": MODEL,
        "input": follow_input,
        "tools": [GET_WEATHER_TOOL],
        "stream": False,
    }
    # Prefer previous_response_id when present (stateful follow-up).
    prev_id = data.get("id")
    if prev_id:
        alt_body = {
            "model": MODEL,
            "previous_response_id": prev_id,
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": FAKE_WEATHER,
                }
            ],
            "tools": [GET_WEATHER_TOOL],
            "stream": False,
        }
        print(f"\n--- POST follow-up with previous_response_id={prev_id!r} ---")
        follow_resp = _post(url, key, alt_body)
        print(f"HTTP {follow_resp.status_code}")
        if follow_resp.status_code >= 400:
            print("previous_response_id follow-up rejected (verbatim):")
            print(follow_resp.text)
            print("\n--- falling back to full input transcript ---")
            _print_json("request body (follow-up transcript)", follow_body)
            follow_resp = _post(url, key, follow_body)
            print(f"HTTP {follow_resp.status_code}")
            used_body = follow_body
        else:
            used_body = alt_body
    else:
        _print_json("request body (follow-up transcript)", follow_body)
        follow_resp = _post(url, key, follow_body)
        print(f"HTTP {follow_resp.status_code}")
        used_body = follow_body

    if follow_resp.status_code >= 400:
        print("\n=== FOLLOW-UP REJECTED (verbatim) ===")
        print(follow_resp.text)
        return 1

    try:
        follow_data = follow_resp.json()
    except json.JSONDecodeError:
        print("\n=== follow-up non-JSON (verbatim) ===")
        print(follow_resp.text)
        return 1

    _print_json("request body (follow-up used)", used_body)
    _print_json("raw response (follow-up)", follow_data)
    follow_items = _output_items(follow_data)
    _print_json("output items (follow-up)", follow_items)
    answer = _final_text(follow_items)
    print("\n=== final answer ===")
    print(answer if answer else "(no output_text message found)")

    print("\n=== field report ===")
    print("Request fields sent and accepted on first turn:")
    for key_name in sorted(request_body.keys()):
        print(f"  - {key_name}: {type(request_body[key_name]).__name__}")
    print(f"  tool_choice accepted as: {meta.get('accepted_tool_choice')!r}")
    print("First-turn response top-level fields:")
    for key_name in sorted(data.keys()):
        val = data[key_name]
        print(f"  - {key_name}: {type(val).__name__}")
    if call:
        print("function_call item fields:")
        for key_name in sorted(call.keys()):
            print(f"  - {key_name}: {call[key_name]!r}")
    print("Follow-up request fields used:")
    for key_name in sorted(used_body.keys()):
        print(f"  - {key_name}: {type(used_body[key_name]).__name__}")
    print("Follow-up response top-level fields:")
    for key_name in sorted(follow_data.keys()):
        print(f"  - {key_name}: {type(follow_data[key_name]).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
