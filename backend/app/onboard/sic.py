"""Map SEC SIC codes to Kernel sector labels used by metric thresholds."""

from __future__ import annotations


def sector_from_sic(sic: int | str | None, sic_description: str | None = None) -> str:
    """Return a coarse sector label from SIC (and optional description).

    Aligns with seed sectors where possible (Utilities, Technology,
    Telecommunications, Industrials, …) so threshold bands can apply.
    """
    desc = (sic_description or "").lower()
    if "telephone" in desc or "telecom" in desc or "communications" in desc:
        return "Telecommunications"
    if "electric" in desc or "gas" in desc or "utility" in desc or "water" in desc:
        return "Utilities"
    if "software" in desc or "computer" in desc or "semiconductor" in desc:
        return "Technology"
    if "pharmaceutical" in desc or "biological" in desc:
        return "Healthcare"
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return "Unknown"
    # SIC major groups (standard industrial classification).
    if 4800 <= code <= 4899:
        return "Telecommunications"
    if 4900 <= code <= 4999:
        return "Utilities"
    if 7370 <= code <= 7379 or 3570 <= code <= 3579:
        return "Technology"
    if 6000 <= code <= 6799:
        return "Financials"
    if 2800 <= code <= 2899 or 2830 <= code <= 2836:
        return "Healthcare"
    if 1000 <= code <= 1499:
        return "Materials"
    if 1500 <= code <= 1799:
        return "Industrials"
    if 2000 <= code <= 3999:
        return "Industrials"
    if 4000 <= code <= 4799:
        return "Industrials"
    if 5000 <= code <= 5999:
        return "Consumer"
    if 7000 <= code <= 8999:
        return "Services"
    return "Unknown"
