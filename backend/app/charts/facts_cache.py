"""Load companyfacts for chart cross-checks (fixtures preferred, live SEC fallback)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.sources.edgar import pad_cik

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "xbrl"


def fixture_companyfacts(cik: str) -> dict[str, Any] | None:
    padded = pad_cik(cik)
    path = _FIXTURES / f"CIK{padded}.json"
    if not path.exists():
        # Also try bare CIK filenames used in older fixtures.
        alt = _FIXTURES / f"CIK{cik.lstrip('0')}.json"
        path = alt if alt.exists() else path
    if not path.exists():
        return None
    return json.loads(path.read_text())


@lru_cache(maxsize=32)
def _live_companyfacts(cik: str) -> dict[str, Any] | None:
    settings = get_settings()
    ua = (settings.sec_user_agent or "").strip()
    if not ua:
        return None
    from app.sources.xbrl import XbrlClient

    try:
        return XbrlClient(user_agent=ua).company_facts(cik)
    except Exception:
        return None


def load_companyfacts(cik: str | None, *, allow_live: bool = True) -> dict[str, Any] | None:
    if not cik:
        return None
    doc = fixture_companyfacts(cik)
    if doc is not None:
        return doc
    if allow_live:
        return _live_companyfacts(pad_cik(cik))
    return None
