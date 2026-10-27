import io

from PIL import Image

from app.llm.sam import Region
from app.tools.charts import BarReading, assign_bars, crop, verify_proportional

W, H = 2812, 1568


def box(x1, y1, x2, y2) -> Region:
    return Region(0, x1, y1, x2, y2, W, H, 0.8)


# Bars with a shared baseline at y=728, heights proportional to 1.25 / 1.38 / 3.00 / 3.35.
BASE = 728
UNIT = 100  # px per 1.0
EPS = [1.25, 1.38, 3.00, 3.35]
BARS = [box(550 + i * 140, BASE - v * UNIT, 680 + i * 140, BASE) for i, v in enumerate(EPS)]
CHART = box(533, 380, 1143, 735)


def readings(values):
    return [BarReading(category=f"c{i}", series=None, value=v) for i, v in enumerate(values)]


def test_assign_bars_keeps_bars_inside_charts_and_drops_legend_swatches():
    legend = box(600, 1500, 650, 1540)
    shuffled = [BARS[2], legend, BARS[0], BARS[3], BARS[1]]
    assigned = assign_bars([CHART], shuffled)
    assert assigned[0] == BARS  # sorted left to right, legend dropped


def test_verify_accepts_correct_reading():
    status, reason, checks = verify_proportional(readings(EPS), BARS)
    assert status == "verified" and reason is None
    assert all(c.deviation < 0.01 for c in checks)


def test_verify_flags_a_misread_label():
    wrong = [1.25, 1.38, 3.00, 2.35]  # 3.35 misread as 2.35
    status, reason, checks = verify_proportional(readings(wrong), BARS)
    assert status == "mismatch"
    assert [c.ok for c in checks] == [True, True, True, False]
    assert "read 2.35" in reason


def test_verify_refuses_when_counts_differ():
    status, reason, _ = verify_proportional(readings(EPS[:3]), BARS)
    assert status == "unverified" and "SAM found 4" in reason


def test_verify_refuses_without_shared_baseline():
    offset = [*BARS[:3], box(970, 300, 1100, 600)]
    status, reason, _ = verify_proportional(readings(EPS), offset)
    assert status == "unverified" and "baseline" in reason


def test_crop_scales_from_frame_to_image_and_pads():
    image = Image.new("RGB", (W // 2, H // 2), "white")  # image at half SAM's frame size
    png = crop(image, CHART)
    cropped = Image.open(io.BytesIO(png))
    chart_w, chart_h = (CHART.x2 - CHART.x1) / 2, (CHART.y2 - CHART.y1) / 2
    assert cropped.width > chart_w and cropped.height > chart_h
    assert cropped.width <= chart_w * 1.25 + 2
