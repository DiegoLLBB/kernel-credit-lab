"""Verifiable chart reading: SAM 3.1 locates, Muse Spark reads, Python checks.

Pipeline for an image (e.g. an investor-deck slide):
  1. SAM 3.1 segments "bar chart"  -> one box per chart.
  2. SAM 3.1 segments "bar"        -> one box per bar (legend swatches fall outside charts).
  3. Each chart is cropped and sent to Muse Spark (with the full slide for legend/context),
     which returns the bars left-to-right with their values as strict JSON.
  4. Geometry check (deterministic): with a shared zero baseline, bar heights must be
     proportional to the values Spark read. Any bar whose implied value deviates beyond the
     tolerance is flagged, so a misread label cannot pass silently.
"""

from __future__ import annotations

import io
import statistics
from dataclasses import dataclass, field

from PIL import Image
from pydantic import BaseModel, Field

from app.llm.client import LLMClient, LLMResult, Provider
from app.llm.sam import Region, image_data_url, segment_image

CHART_PROMPT = "bar chart"
BAR_PROMPT = "bar"
PROMPT_VERSION = "chart_reader_v2"


# --------------------------------------------------------------------------- schemas
class BarReading(BaseModel):
    category: str = Field(description="x-axis category under the bar, e.g. 'Q2' or '2027'")
    series: str | None = Field(description="legend series of the bar, e.g. '2025', or null")
    value: float | None = Field(description="value printed on or above the bar, or null")


class ChartExtraction(BaseModel):
    title: str | None
    unit: str | None = Field(description="unit of the values, e.g. 'USD per share', '$bn'")
    categories: list[str] = Field(
        description="x-axis label of EVERY bar left to right, including unlabelled bars",
        default_factory=list,
    )
    values: list[float | None] = Field(
        description="printed value of each bar, aligned with categories; null if none printed",
        default_factory=list,
    )
    value_labels: list[str | None] = Field(
        description="printed labels on/above each bar when present; else null",
        default_factory=list,
    )

    def to_bar_readings(self) -> list[BarReading]:
        """Bars left-to-right for geometry check."""
        n = min(len(self.categories), len(self.values))
        return [
            BarReading(
                category=self.categories[i],
                series=None,
                value=None if self.values[i] is None else float(self.values[i]),
            )
            for i in range(n)
        ]

    def printed(self) -> tuple[list[str], list[float]]:
        """Only the bars with a printed value: the pairs that can be checked and cited."""
        pairs = [
            (c, float(v))
            for c, v in zip(self.categories, self.values, strict=False)
            if v is not None
        ]
        return [c for c, _ in pairs], [v for _, v in pairs]


SYSTEM_PROMPT = (
    "You read bar charts from financial presentations. The FIRST image is a crop containing "
    "exactly one chart; the SECOND image is the full slide for context (titles, legend). "
    "Read only the chart in the first image. Return title, unit, categories (the x-axis label "
    "of EVERY bar, left to right, one entry per bar), values (aligned with categories: the "
    "number printed on or above that bar, or null when the bar has no printed number) and "
    "value_labels (the printed text, or null). Never estimate a value that is not printed; "
    "use null instead, but keep the bar in the list. "
    "categories and values must have the same length."
)


# --------------------------------------------------------------------------- results
@dataclass
class BarCheck:
    reading: BarReading
    box: Region
    implied_value: float
    deviation: float  # relative difference between implied and read value
    ok: bool
    allowed: float = 0.0  # absolute value difference accepted for this bar


@dataclass
class ChartResult:
    chart_box: Region
    extraction: ChartExtraction | None
    bar_boxes: list[Region]
    checks: list[BarCheck] = field(default_factory=list)
    status: str = "unverified"  # verified | mismatch | unverified
    reason: str | None = None
    llm: LLMResult | None = None


# --------------------------------------------------------------------------- geometry
def _center(r: Region) -> tuple[float, float]:
    return (r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2


def _contains(outer: Region, inner: Region, pad: float = 0.02) -> bool:
    w, h = outer.x2 - outer.x1, outer.y2 - outer.y1
    cx, cy = _center(inner)
    return (outer.x1 - pad * w) <= cx <= (outer.x2 + pad * w) and (outer.y1 - pad * h) <= cy <= (
        outer.y2 + pad * h
    )


def assign_bars(charts: list[Region], bars: list[Region]) -> dict[int, list[Region]]:
    """Map each chart (by index) to the bars whose centre lies inside it, left to right.

    Bars outside every chart (typically legend swatches) are dropped.
    """
    assigned: dict[int, list[Region]] = {i: [] for i in range(len(charts))}
    for bar in bars:
        for i, chart in enumerate(charts):
            if _contains(chart, bar):
                assigned[i].append(bar)
                break
    for i in assigned:
        assigned[i].sort(key=lambda b: (b.x1 + b.x2) / 2)
    return assigned


def merge_split_charts(
    charts: list[Region], bars: list[Region], baseline_tol: float = 0.02
) -> list[Region]:
    """Join chart boxes that SAM split into one box per bar.

    SAM sometimes returns each bar of a sparse chart (e.g. two tall bars) as its own
    "bar chart". Boxes are joined only when each holds at most one bar, they sit on the
    same baseline and the gap between them is no wider than the wider box. A wrong join
    cannot verify silently: the proportionality check still has to pass on the result.
    """
    if len(charts) < 2:
        return list(charts)
    merged = sorted(charts, key=lambda c: c.x1)
    changed = True
    while changed:
        changed = False
        counts = assign_bars(merged, bars)
        pairs = [(i, j) for i in range(len(merged)) for j in range(i + 1, len(merged))]
        for i, j in pairs:
            a, b = merged[i], merged[j]
            same_base = abs(a.y2 - b.y2) <= baseline_tol * a.frame_height
            gap = max(b.x1 - a.x2, a.x1 - b.x2)
            close = 0 <= gap <= max(a.x2 - a.x1, b.x2 - b.x1)
            sparse = len(counts[i]) <= 1 and len(counts[j]) <= 1
            if same_base and close and sparse:
                union = Region(
                    object_id=a.object_id,
                    x1=min(a.x1, b.x1),
                    y1=min(a.y1, b.y1),
                    x2=max(a.x2, b.x2),
                    y2=max(a.y2, b.y2),
                    frame_width=a.frame_width,
                    frame_height=a.frame_height,
                    confidence=min(a.confidence or 0, b.confidence or 0) or None,
                )
                merged = [c for k, c in enumerate(merged) if k not in (i, j)] + [union]
                merged.sort(key=lambda c: c.x1)
                changed = True
                break
    return merged


def verify_proportional(
    readings: list[BarReading],
    boxes: list[Region],
    tolerance: float = 0.08,
    pixel_slack: float = 2.0,
) -> tuple[str, str | None, list[BarCheck]]:
    """Check bar heights against read values, assuming a shared zero baseline.

    A bar passes when its implied value is within ``tolerance`` (relative) of the value
    read, or within ``pixel_slack`` pixels of bar height: a 7-px bar cannot be measured
    to 8%, and the check should not claim more precision than the image has.
    """
    if not readings or not boxes:
        return "unverified", "no bars detected or read", []
    if len(readings) != len(boxes):
        # A printed 0.0 has no visible bar; drop zeros if that reconciles the counts.
        nonzero = [r for r in readings if r.value != 0]
        if len(nonzero) == len(boxes):
            readings = nonzero
        else:
            return (
                "unverified",
                f"bar count differs: SAM found {len(boxes)}, Spark read {len(readings)}",
                [],
            )
    pairs = [(r, b) for r, b in zip(readings, boxes, strict=True) if r.value is not None]
    if len(pairs) < 2:
        return "unverified", "fewer than two printed values: nothing to compare", []
    if any(r.value is not None and r.value <= 0 for r, _ in pairs):
        return "unverified", "non-positive values: zero-baseline check does not apply", []

    heights = [b.y2 - b.y1 for b in boxes]
    tallest = max(heights)
    baselines = [b.y2 for b in boxes]
    if max(baselines) - min(baselines) > 0.05 * tallest:
        return "unverified", "bars do not share a baseline (stacked or offset chart)", []

    scale = statistics.median(r.value / (b.y2 - b.y1) for r, b in pairs)
    checks: list[BarCheck] = []
    for reading, box in pairs:
        height = box.y2 - box.y1
        implied = height * scale
        diff = abs(implied - reading.value)
        allowed = max(tolerance * reading.value, pixel_slack * scale)
        deviation = diff / reading.value
        checks.append(BarCheck(reading, box, implied, deviation, diff <= allowed, allowed))
    if all(c.ok for c in checks):
        return "verified", None, checks
    bad = [c for c in checks if not c.ok]
    detail = "; ".join(
        f"{c.reading.category}/{c.reading.series}: read {c.reading.value:g}, "
        f"geometry implies {c.implied_value:.3g}"
        for c in bad
    )
    return "mismatch", detail, checks


def crop(image: Image.Image, box: Region, pad_x: float = 0.12, pad_y: float = 0.35) -> bytes:
    """Crop a region (in SAM's frame coordinates) with padding for labels and titles."""
    sx, sy = image.width / box.frame_width, image.height / box.frame_height
    w, h = (box.x2 - box.x1) * sx, (box.y2 - box.y1) * sy
    left = max(0, box.x1 * sx - pad_x * w)
    top = max(0, box.y1 * sy - pad_y * h)
    right = min(image.width, box.x2 * sx + pad_x * w)
    bottom = min(image.height, box.y2 * sy + pad_y * h)
    buf = io.BytesIO()
    image.crop((int(left), int(top), int(right), int(bottom))).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- pipeline
async def read_charts(
    client: LLMClient,
    image_bytes: bytes,
    *,
    filename: str = "slide.png",
    run_id: str | None = None,
    step_id: str | None = None,
) -> list[ChartResult]:
    settings = client.settings
    image = Image.open(io.BytesIO(image_bytes))
    charts, _ = await segment_image(
        client, image_bytes, CHART_PROMPT, filename=filename, run_id=run_id, step_id=step_id
    )
    if not charts:
        return []
    bars, _ = await segment_image(
        client, image_bytes, BAR_PROMPT, filename=filename, run_id=run_id, step_id=step_id
    )
    charts = merge_split_charts(charts, bars)
    charts.sort(key=lambda c: (round(c.y1 / max(c.frame_height, 1), 1), c.x1))
    by_chart = assign_bars(charts, bars)
    full_slide = image_data_url(image_bytes, filename)

    results: list[ChartResult] = []
    for i, chart in enumerate(charts):
        crop_url = image_data_url(crop(image, chart), "chart.png")
        extraction, llm = await client.structured_json(
            provider=Provider.META,
            model=settings.default_model,
            schema=ChartExtraction,
            task="read_chart",
            prompt_version=PROMPT_VERSION,
            run_id=run_id,
            step_id=step_id,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Chart crop, then full slide:"},
                        {"type": "image_url", "image_url": {"url": crop_url}},
                        {"type": "image_url", "image_url": {"url": full_slide}},
                    ],
                },
            ],
        )
        readings = extraction.to_bar_readings()
        status, reason, checks = verify_proportional(readings, by_chart[i])
        results.append(
            ChartResult(
                chart_box=chart,
                extraction=extraction,
                bar_boxes=by_chart[i],
                checks=checks,
                status=status,
                reason=reason,
                llm=llm,
            )
        )
    return results
