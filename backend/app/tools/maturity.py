"""Maturity-wall helpers: map instrument maturity dates onto fiscal-year schedule buckets."""

from __future__ import annotations

from datetime import date
from typing import Any


def _period_end(bucket: dict[str, Any]) -> date | None:
    raw = bucket.get("period_end")
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def bucket_for(maturity: date, buckets: list[dict[str, Any]]) -> str | None:
    """Return the schedule bucket label whose fiscal range contains ``maturity``.

    Dated buckets are half-open on the left: ``(previous.period_end, this.period_end]``.
    The first dated bucket is open on the left (``(-∞, period_end]``). Dates after the
    last dated ``period_end`` map to the undated "after" bucket when one is present.
    """
    dated: list[tuple[date, dict[str, Any]]] = []
    after: dict[str, Any] | None = None
    for bucket in buckets:
        end = _period_end(bucket)
        if end is None:
            after = bucket
        else:
            dated.append((end, bucket))
    dated.sort(key=lambda item: item[0])

    previous_end: date | None = None
    for end, bucket in dated:
        if (previous_end is None or maturity > previous_end) and maturity <= end:
            label = bucket.get("label")
            return str(label) if label is not None else None
        previous_end = end

    if dated and maturity > dated[-1][0] and after is not None:
        label = after.get("label")
        return str(label) if label is not None else None
    return None
