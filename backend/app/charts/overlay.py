"""Draw numbered bar overlays on chart crops / page images."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from app.llm.sam import Region
from app.tools.charts import ChartResult

COLORS = {
    "chart": (37, 99, 235),
    "ok": (22, 163, 74),
    "bad": (220, 38, 38),
    "none": (234, 179, 8),
}


def overlay_png(image_bytes: bytes, results: list[ChartResult]) -> bytes:
    """Full-page overlay: chart boxes + numbered bars (green/red by verification)."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    for n, res in enumerate(results, 1):
        c = res.chart_box
        sx, sy = image.width / c.frame_width, image.height / c.frame_height
        draw.rectangle(
            (c.x1 * sx, c.y1 * sy, c.x2 * sx, c.y2 * sy), outline=COLORS["chart"], width=4
        )
        label = f"chart {n}: {res.status}"
        draw.text((c.x1 * sx + 4, c.y1 * sy + 4), label, fill=COLORS["chart"], font=font)
        checks = {id(chk.box): chk for chk in res.checks}
        for i, bar in enumerate(res.bar_boxes, 1):
            chk = checks.get(id(bar))
            color = COLORS["none"] if chk is None else COLORS["ok" if chk.ok else "bad"]
            box = (bar.x1 * sx, bar.y1 * sy, bar.x2 * sx, bar.y2 * sy)
            draw.rectangle(box, outline=color, width=3)
            draw.text((box[0] + 2, box[1] + 2), str(i), fill=color, font=font)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def crop_overlay_png(
    page_bytes: bytes,
    chart_box: Region,
    bar_boxes: list[Region],
    *,
    checks_ok: list[bool] | None = None,
    pad_x: float = 0.12,
    pad_y: float = 0.35,
) -> bytes:
    """Crop around the chart and number its bars on the crop."""
    from app.tools.charts import crop

    image = Image.open(io.BytesIO(page_bytes)).convert("RGB")
    crop_bytes = crop(image, chart_box, pad_x=pad_x, pad_y=pad_y)
    cropped = Image.open(io.BytesIO(crop_bytes)).convert("RGB")
    draw = ImageDraw.Draw(cropped)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None

    # Map bar boxes from page frame coords into the padded crop coordinate system.
    sx = image.width / chart_box.frame_width
    sy = image.height / chart_box.frame_height
    w, h = (chart_box.x2 - chart_box.x1) * sx, (chart_box.y2 - chart_box.y1) * sy
    left = max(0, chart_box.x1 * sx - pad_x * w)
    top = max(0, chart_box.y1 * sy - pad_y * h)

    for i, bar in enumerate(bar_boxes, 1):
        ok = True if checks_ok is None or i - 1 >= len(checks_ok) else checks_ok[i - 1]
        color = COLORS["ok" if ok else "bad"]
        x1 = bar.x1 * sx - left
        y1 = bar.y1 * sy - top
        x2 = bar.x2 * sx - left
        y2 = bar.y2 * sy - top
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        draw.text((x1 + 2, y1 + 2), str(i), fill=color, font=font)

    buf = io.BytesIO()
    cropped.save(buf, "PNG")
    return buf.getvalue()
