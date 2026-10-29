"""Versioned system prompts for the agent loop."""

from __future__ import annotations

from datetime import date
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
EVENT_V1_PATH = PROMPT_DIR / "event_v1.md"
EVENT_V2_PATH = PROMPT_DIR / "event_v2.md"
EVENT_V3_PATH = PROMPT_DIR / "event_v3.md"
EVENT_V4_PATH = PROMPT_DIR / "event_v4.md"
EVENT_V5_PATH = PROMPT_DIR / "event_v5.md"
PROMPT_VERSION = "event_v5"


def load_event_prompt(
    version: str = PROMPT_VERSION,
    *,
    today: date | str | None = None,
) -> str:
    path = PROMPT_DIR / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    text = path.read_text(encoding="utf-8")
    if today is not None:
        stamp = today.isoformat() if isinstance(today, date) else str(today)
        text = text.replace("{available_from}", stamp)
    return text
