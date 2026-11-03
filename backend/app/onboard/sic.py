"""Map SEC SIC codes to GICS-style sector labels (fallback only).

Prefer ``seed/issuer_sectors.yaml`` via ``resolve_sector``. SIC results are
labelled ``(SIC)`` by the caller so they are never confused with the map.
"""

from __future__ import annotations


def sector_from_sic(sic: int | str | None, sic_description: str | None = None) -> str:
    """Return a coarse GICS-style sector label from SIC (and optional description)."""
    desc = (sic_description or "").lower()
    if "telephone" in desc or "telecom" in desc or "communications" in desc:
        return "Communication Services"
    if "electric" in desc or "gas" in desc or "utility" in desc or "water" in desc:
        return "Utilities"
    if "software" in desc or "computer" in desc or "semiconductor" in desc:
        return "Information Technology"
    if "pharmaceutical" in desc or "biological" in desc or "medicinal" in desc:
        return "Health Care"
    if "petroleum" in desc or "crude" in desc or ("oil" in desc and "gas" in desc):
        return "Energy"
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return "Unknown"
    # SIC major groups → GICS-style labels used by the ticker map / thresholds.
    if 4800 <= code <= 4899:
        return "Communication Services"
    if 4900 <= code <= 4999:
        return "Utilities"
    if 7370 <= code <= 7379 or 3570 <= code <= 3579:
        return "Information Technology"
    if 6000 <= code <= 6799:
        return "Financials"
    if 2800 <= code <= 2899 or 2830 <= code <= 2836 or 3840 <= code <= 3845:
        return "Health Care"
    if 1000 <= code <= 1499:
        return "Materials"
    if 1300 <= code <= 1399 or code == 2911:
        return "Energy"
    if 1500 <= code <= 1799:
        return "Industrials"
    if 2000 <= code <= 2099 or 5400 <= code <= 5499:
        return "Consumer Staples"
    if 2000 <= code <= 3999:
        return "Industrials"
    if 4000 <= code <= 4799:
        return "Industrials"
    if 5000 <= code <= 5999:
        return "Consumer Discretionary"
    if 7000 <= code <= 8999:
        return "Consumer Discretionary"
    return "Unknown"
