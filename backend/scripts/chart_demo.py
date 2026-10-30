"""Read every bar chart in an image and verify the reading against bar geometry.

Usage (from backend/):
    python -m scripts.chart_demo slide.png
Writes <image>_annotated.png next to the input: charts in blue, verified bars in green,
flagged bars in red.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from app.config import get_settings
from app.llm import LLMClient
from app.tools.charts import ChartResult, read_charts

COLORS = {"chart": (37, 99, 235), "ok": (22, 163, 74), "bad": (220, 38, 38), "none": (234, 179, 8)}


def annotate(path: Path, results: list[ChartResult]) -> Path:
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for n, res in enumerate(results, 1):
        c = res.chart_box
        sx, sy = image.width / c.frame_width, image.height / c.frame_height
        draw.rectangle(
            (c.x1 * sx, c.y1 * sy, c.x2 * sx, c.y2 * sy), outline=COLORS["chart"], width=5
        )
        draw.text((c.x1 * sx + 6, c.y1 * sy + 6), f"chart {n}: {res.status}", fill=COLORS["chart"])
        checks = {id(chk.box): chk for chk in res.checks}
        for bar in res.bar_boxes:
            chk = checks.get(id(bar))
            color = COLORS["none"] if chk is None else COLORS["ok" if chk.ok else "bad"]
            draw.rectangle(
                (bar.x1 * sx, bar.y1 * sy, bar.x2 * sx, bar.y2 * sy), outline=color, width=4
            )
    out = path.with_name(f"{path.stem}_annotated.png")
    image.save(out)
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()

    async with LLMClient(get_settings()) as client:
        results = await read_charts(client, args.image.read_bytes(), filename=args.image.name)

    if not results:
        print("No bar charts found.")
        return 1
    for n, res in enumerate(results, 1):
        ex = res.extraction
        print(f"\nChart {n}: {ex.title if ex else '?'} ({ex.unit if ex else '?'})")
        n_spark = len(ex.to_bar_readings()) if ex else 0
        print(f"  SAM bars: {len(res.bar_boxes)} | Spark bars: {n_spark}")
        if res.checks:
            for chk in res.checks:
                mark = "✓" if chk.ok else "✗"
                r = chk.reading
                print(
                    f"  {mark} {r.category:<10} {str(r.series):<8} read={r.value:<8g} "
                    f"geometry={chk.implied_value:<8.3g} dev={chk.deviation:.1%}"
                )
        else:
            for r in ex.to_bar_readings() if ex else []:
                print(f"  · {r.category:<10} {str(r.series):<8} read={r.value:g}")
        print(f"  => {res.status.upper()}" + (f": {res.reason}" if res.reason else ""))
        if res.llm:
            tokens = f"{res.llm.input_tokens}/{res.llm.output_tokens}"
            print(f"  (Spark {res.llm.latency_ms} ms, {tokens} tok)")
    print(f"\nAnnotated image: {annotate(args.image, results)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
