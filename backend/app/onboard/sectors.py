"""Resolve issuer sector: explicit GICS map first, SIC fallback labelled."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.onboard.sic import sector_from_sic

_CONFIG_RELATIVE = Path("seed") / "issuer_sectors.yaml"
_cache: dict[str, Any] | None = None


def _config_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _CONFIG_RELATIVE
        if candidate.exists():
            return candidate
    return here.parents[2] / _CONFIG_RELATIVE


def load_sector_map() -> dict[str, str]:
    """Uppercase ticker → GICS-style sector label."""
    global _cache
    if _cache is None:
        raw = yaml.safe_load(_config_path().read_text()) or {}
        tickers = raw.get("tickers") or {}
        _cache = {str(k).strip().upper(): str(v).strip() for k, v in tickers.items() if k and v}
    return _cache


def sector_map_source() -> str:
    raw = yaml.safe_load(_config_path().read_text()) or {}
    return str(raw.get("source") or "GICS map")


def resolve_sector(
    ticker: str | None,
    sic: int | str | None = None,
    sic_description: str | None = None,
) -> tuple[str, str]:
    """Return ``(sector, source)``.

    ``source`` is ``gics_map`` for the explicit ticker map, or ``sic`` when
    falling back (sector label then ends with `` (SIC)``).
    """
    wanted = (ticker or "").strip().upper()
    mapped = load_sector_map().get(wanted) if wanted else None
    if mapped:
        return mapped, "gics_map"
    base = sector_from_sic(sic, sic_description)
    if not base or base == "Unknown":
        return "Unknown (SIC)", "sic"
    return f"{base} (SIC)", "sic"


def apply_sector_map(session: Any) -> dict[str, int]:
    """Re-stamp root issuers from the GICS map (and leave SIC-labelled alone).

    Returns counts: updated / unchanged / no_ticker.
    """
    from sqlalchemy import select

    from app.models import Issuer

    stats = {"updated": 0, "unchanged": 0, "no_ticker": 0}
    mapping = load_sector_map()
    for iss in session.scalars(select(Issuer).where(Issuer.parent_id.is_(None))).all():
        ticker = (iss.ticker or "").strip().upper()
        if not ticker:
            # Fall back to key uppercased when it looks like a ticker key.
            ticker = (iss.key or "").strip().upper()
        if not ticker:
            stats["no_ticker"] += 1
            continue
        sector, _src = resolve_sector(ticker)
        # Only force-update when the map has an explicit entry; otherwise keep
        # whatever SIC/onboard already wrote (may already be labelled).
        if ticker not in mapping:
            stats["unchanged"] += 1
            continue
        if iss.sector == sector:
            stats["unchanged"] += 1
            continue
        iss.sector = sector
        stats["updated"] += 1
    return stats
