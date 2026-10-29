"""Document text helpers for agent read_document."""

from __future__ import annotations

import re
from html import unescape

PAGE_CHARS = 8000

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.I | re.S)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def page_count(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + PAGE_CHARS - 1) // PAGE_CHARS)


def page_slice(text: str, page: int) -> tuple[str, int, int]:
    """Return ``(chunk, start, end)`` for 0-based ``page``."""
    start = page * PAGE_CHARS
    end = min(len(text), start + PAGE_CHARS)
    return text[start:end], start, end
