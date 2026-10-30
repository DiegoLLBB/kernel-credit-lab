import io

from PIL import Image

from app.llm.sam import Region
from app.tools.charts import (
    BarReading,
    ChartExtraction,
    assign_bars,
    crop,
    merge_split_charts,
    verify_proportional,
)

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


def test_verify_tolerates_pixel_error_on_short_bars():
    # Verizon 3Q26 p4 shape: 44 and 55 drawn ~7 px tall next to a 616 bar.
    unit = 0.15  # px per 1K
    vals = [44, 616, 55, 184, 406]
    drawn = [44 + 6.7, 616, 55 + 6.7, 184, 406]  # one px of SAM error on the short bars
    bars = [box(100 + i * 150, BASE - v * unit, 200 + i * 150, BASE) for i, v in enumerate(drawn)]
    status, reason, checks = verify_proportional(readings(vals), bars)
    assert status == "verified", reason
    assert checks[0].allowed > 0.08 * 44  # pixel allowance, not the 8% rule, let it pass


def test_pixel_slack_still_catches_a_misread_short_bar():
    unit = 0.15
    vals = [44, 616, 55, 184, 406]
    bars = [box(100 + i * 150, BASE - v * unit, 200 + i * 150, BASE) for i, v in enumerate(vals)]
    wrong = [440, 616, 55, 184, 406]  # 44 misread as 440
    status, _, checks = verify_proportional(readings(wrong), bars)
    assert status == "mismatch" and not checks[0].ok


def test_merge_split_charts_joins_one_box_per_bar():
    # Verizon 3Q26 p5 shape: SAM returned each of two tall bars as its own "bar chart".
    left, right = box(190, 383, 342, 730), box(444, 383, 598, 730)
    bars = [box(200, 400, 330, 728), box(455, 390, 590, 728)]
    merged = merge_split_charts([right, left], bars)
    assert len(merged) == 1
    assert (merged[0].x1, merged[0].x2) == (190, 598)
    assert assign_bars(merged, bars)[0] == bars


def test_merge_leaves_real_charts_alone():
    stacked = [CHART, box(533, 800, 1143, 1150)]  # two charts, one above the other
    assert merge_split_charts(stacked, BARS) == sorted(stacked, key=lambda c: c.x1)
    side = [CHART, box(1200, 380, 1800, 735)]  # neighbour chart with several bars
    more = [*BARS, box(1250, 500, 1350, 728), box(1400, 450, 1500, 728)]
    assert len(merge_split_charts(side, more)) == 2


def test_verify_uses_only_printed_bars():
    # Amazon deck shape: five quarters drawn, only the first and last carry a printed value.
    vals = [180.2, None, None, None, 201.9]
    drawn = [180.2, 189.0, 175.0, 197.0, 201.9]
    unit = 1.5
    bars = [box(100 + i * 150, BASE - v * unit, 200 + i * 150, BASE) for i, v in enumerate(drawn)]
    status, reason, checks = verify_proportional(readings(vals), bars)
    assert status == "verified", reason
    assert [c.reading.value for c in checks] == [180.2, 201.9]
    wrong = [180.2, None, None, None, 120.9]
    status, _, _ = verify_proportional(readings(wrong), bars)
    assert status == "mismatch"


def test_one_printed_value_is_not_verifiable():
    # A lone bar is trivially "proportional" to itself; refuse instead of passing.
    status, reason, _ = verify_proportional(readings([2.0]), [BARS[0]])
    assert status == "unverified" and "two printed values" in reason
    status, _, _ = verify_proportional(readings([1.25, None, None, None]), BARS)
    assert status == "unverified"


def test_printed_zero_without_a_bar_is_dropped():
    # Duke p24 shape: "0.0%" is printed but no bar is drawn for it.
    vals = [1.25, 0.0, 1.38, 3.00, 3.35]
    status, reason, checks = verify_proportional(readings(vals), BARS)
    assert status == "verified", reason
    assert len(checks) == 4


def test_extraction_printed_keeps_only_cited_pairs():
    ex = ChartExtraction(
        title="Net Sales",
        unit="$B",
        categories=["Q3 2025", "Q4 2025", "Q3 2026"],
        values=[180.2, None, 201.9],
        value_labels=["$180.2", None, "$201.9"],
    )
    assert ex.printed() == (["Q3 2025", "Q3 2026"], [180.2, 201.9])
    assert [r.value for r in ex.to_bar_readings()] == [180.2, None, 201.9]


def test_merge_finds_halves_separated_by_another_chart_in_sort_order():
    # Duke p9 shape: a two-bar chart above, a split two-bar chart below.
    above = box(1076, 323, 1497, 507)
    left, right = box(1076, 956, 1239, 1048), box(1335, 820, 1497, 1048)
    bars = [
        box(1090, 400, 1220, 505),
        box(1350, 340, 1480, 505),
        box(1090, 960, 1230, 1046),
        box(1345, 830, 1490, 1046),
    ]
    merged = merge_split_charts([left, above, right], bars)
    assert len(merged) == 2
    lower = [c for c in merged if c.y2 == 1048][0]
    assert (lower.x1, lower.x2) == (1076, 1497)
