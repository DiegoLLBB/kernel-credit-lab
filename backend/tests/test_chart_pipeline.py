"""Chart pipeline + read_chart + chart_reading evidence (fake SAM/Spark)."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.chart_evidence import chart_reading_verified, fact_supports_amount
from app.agent.tools.changes import propose_change
from app.agent.tools.charts_tool import read_chart
from app.agent.tools.checklist import set_checklist
from app.agent.tools.facts import record_fact
from app.charts.pipeline import process_page_charts, render_pdf_page
from app.db import Base
from app.llm.sam import Region
from app.models import ChartReading, Event, EventFiling, Filing, Instrument, Issuer
from app.models.trace import AgentRun
from app.models.verification import Fact
from app.tools.charts import ChartExtraction, ChartResult, verify_proportional

W, H = 800, 600
BASE = 500
UNIT = 80
VALUES = [1.25, 1.38, 3.00, 3.35]
CATEGORIES = ["c0", "c1", "c2", "c3"]


def _box(x1: float, y1: float, x2: float, y2: float) -> Region:
    return Region(0, x1, y1, x2, y2, float(W), float(H), 0.9)


CHART = _box(100, 120, 700, 520)
BARS = [_box(150 + i * 120, BASE - v * UNIT, 230 + i * 120, BASE) for i, v in enumerate(VALUES)]


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def _slide_png() -> bytes:
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((CHART.x1, CHART.y1, CHART.x2, CHART.y2), outline="blue", width=2)
    for bar in BARS:
        draw.rectangle((bar.x1, bar.y1, bar.x2, bar.y2), fill="steelblue")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _tiny_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _chart_result(*, values: list[float] | None = None) -> ChartResult:
    vals = values if values is not None else VALUES
    extraction = ChartExtraction(
        title="EPS",
        unit="$",
        categories=CATEGORIES[: len(vals)],
        values=list(vals),
        value_labels=[str(v) for v in vals],
    )
    readings = extraction.to_bar_readings()
    st, reason, checks = verify_proportional(readings, BARS[: len(vals)])
    return ChartResult(
        chart_box=CHART,
        extraction=extraction,
        bar_boxes=BARS[: len(vals)],
        checks=checks,
        status=st,
        reason=reason,
        llm=SimpleNamespace(
            model="test",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            cost_usd=Decimal("0.001"),
        ),
    )


def _seed_event(session: Session) -> tuple[Filing, AgentRun, Instrument]:
    issuer = Issuer(key="duke_corp", name="Duke Energy Corporation", ticker="DUK")
    session.add(issuer)
    session.flush()
    filing = Filing(
        issuer_id=issuer.id,
        form_type="presentation",
        url="https://example.com/deck.pdf",
        title="Test Deck",
        filed_at=date(2026, 8, 4),
        body_text=(
            "The Test Notes due 2028 with coupon 3.000% mature in 2028; "
            "outstanding amount updated on the slide."
        ),
        content_bytes=_tiny_pdf(),
        content_hash="abc",
    )
    session.add(filing)
    session.flush()
    event = Event(
        event_type="presentation",
        issuer_id=issuer.id,
        event_date=date(2026, 8, 4),
        source_filing_id=filing.id,
        status="processing",
    )
    session.add(event)
    session.flush()
    session.add(EventFiling(event_id=event.id, filing_id=filing.id))
    run = AgentRun(
        question="chart",
        workflow="event_monitor",
        mode="standard",
        status="running",
        state="AGENT",
        event_id=event.id,
        metrics_outputs=[],
        pages_read={filing.id: [0]},
    )
    session.add(run)
    session.flush()
    bond = Instrument(
        key="duk_test_2028",
        issuer_id=issuer.id,
        name="Test Notes due 2028",
        instrument_type="bond",
        coupon=Decimal("3.0"),
        maturity=date(2028, 1, 1),
        amount_outstanding=Decimal("1000000000"),
        currency="USD",
        status="active",
    )
    session.add(bond)
    session.flush()
    return filing, run, bond


@pytest.mark.asyncio
async def test_process_page_stores_verified_chart_with_overlay(session: Session) -> None:
    filing, _run, _bond = _seed_event(session)

    async def fake_read(**_k):
        return [_chart_result()]

    rows = await process_page_charts(session, filing, _slide_png(), 0, read_charts_fn=fake_read)
    assert len(rows) == 1
    row = rows[0]
    assert row.verified is True
    assert row.title == "EPS"
    assert row.categories == CATEGORIES
    assert [float(v) for v in row.values] == VALUES
    assert row.crop_png and row.overlay_png
    assert Image.open(io.BytesIO(row.overlay_png)).size[0] > 0


@pytest.mark.asyncio
async def test_mismatched_values_stored_unverified(session: Session) -> None:
    filing, _run, _bond = _seed_event(session)

    async def fake_read(**_k):
        return [_chart_result(values=[1.25, 1.38, 3.00, 2.35])]

    rows = await process_page_charts(session, filing, _slide_png(), 0, read_charts_fn=fake_read)
    assert len(rows) == 1
    assert rows[0].verified is False
    assert rows[0].verification_detail.get("status") == "mismatch"


@pytest.mark.asyncio
async def test_read_chart_returns_only_verified(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    filing, run, _bond = _seed_event(session)

    async def fake_read(**_k):
        return [_chart_result(), _chart_result(values=[1.25, 1.38, 3.00, 2.35])]

    monkeypatch.setattr(
        "app.agent.tools.charts_tool.render_pdf_page",
        lambda *_a, **_k: _slide_png(),
    )
    result = await read_chart(session, run, filing.id, 0, read_charts_fn=fake_read)
    assert result["ok"] is True
    charts = result["charts"]
    assert len(charts) == 1
    assert charts[0]["verified"] is True
    assert charts[0]["values"] == VALUES
    all_rows = session.scalars(select(ChartReading)).all()
    assert len(all_rows) == 2
    assert sum(1 for r in all_rows if r.verified) == 1


def test_evidence_accepts_verified_chart_reading_refuses_unverified(session: Session) -> None:
    filing, run, bond = _seed_event(session)
    items = [
        {"key": "instrument", "question": "which", "evidence_needed": "name"},
        {"key": "amount", "question": "how much", "evidence_needed": "figure"},
        {"key": "redemption_date", "question": "when", "evidence_needed": "date"},
        {"key": "price_basis", "question": "price", "evidence_needed": "par"},
        {"key": "conditions", "question": "conds", "evidence_needed": "none"},
        {"key": "maturity_effect", "question": "wall", "evidence_needed": "effect"},
        {"key": "counter_evidence", "question": "counter", "evidence_needed": "none"},
    ]
    assert set_checklist(session, run, "redemption", items)["ok"]

    good = ChartReading(
        issuer_id=filing.issuer_id,
        filing_id=filing.id,
        page=0,
        bbox={},
        title="EPS",
        unit="$",
        categories=["c0"],
        values=[1250000000.0],
        verified=True,
        verification_detail={"status": "verified"},
        crop_png=b"x",
        overlay_png=b"y",
    )
    bad = ChartReading(
        issuer_id=filing.issuer_id,
        filing_id=filing.id,
        page=0,
        bbox={},
        title="EPS",
        unit="$",
        categories=["c0"],
        values=[1250000000.0],
        verified=False,
        verification_detail={"status": "mismatch", "reason": "geometry"},
    )
    session.add_all([good, bad])
    session.flush()

    ok_rec = record_fact(
        session,
        run,
        "amount",
        filing.id,
        "c0=1250000000",
        "chart value",
        fact_type="chart_reading",
        chart_id=good.id,
        category="c0",
        value=1250000000,
    )
    assert ok_rec["ok"] is True, ok_rec

    refused = record_fact(
        session,
        run,
        "amount",
        filing.id,
        "c0=1250000000",
        "bad chart",
        fact_type="chart_reading",
        chart_id=bad.id,
        category="c0",
        value=1250000000,
    )
    assert refused["ok"] is False
    assert "unverified" in (refused.get("reason") or "").lower()

    good_fact = session.get(Fact, ok_rec["fact_id"])
    assert good_fact is not None
    assert chart_reading_verified(session, good_fact)[0] is True
    assert (
        fact_supports_amount(session, good_fact, 1250000000, quote_check=lambda *_: False) is True
    )

    bad_fact = Fact(
        run_id=run.id,
        checklist_item_id=good_fact.checklist_item_id,
        filing_id=filing.id,
        location=f"chart_reading:{bad.id}",
        exact_quote="c0=1250000000",
        claim="bad",
        fact_type="chart_reading",
        meta={"chart_id": bad.id, "category": "c0", "value": 1250000000.0},
    )
    session.add(bad_fact)
    session.flush()
    assert chart_reading_verified(session, bad_fact)[0] is False
    assert (
        fact_supports_amount(session, bad_fact, 1250000000, quote_check=lambda *_: False) is False
    )

    # propose_change: instrument quote + verified chart amount.
    inst = record_fact(
        session,
        run,
        "instrument",
        filing.id,
        "The Test Notes due 2028 with coupon 3.000% mature in 2028",
        "identifies notes",
    )
    assert inst["ok"], inst
    result = propose_change(
        session,
        run,
        {
            "action": "update_amount",
            "instrument_key": bond.key,
            "effective_date": "2026-08-04",
            "new_amount": "1250000000",
            "evidence": {
                "instrument": inst["fact_id"],
                "amount": good_fact.id,
            },
        },
    )
    assert result["ok"] is True, result

    # Same change with unverified chart fact must fail evidence.
    result_bad = propose_change(
        session,
        run,
        {
            "action": "update_amount",
            "instrument_key": bond.key,
            "effective_date": "2026-08-04",
            "new_amount": "1250000000",
            "evidence": {
                "instrument": inst["fact_id"],
                "amount": bad_fact.id,
            },
        },
    )
    assert result_bad["ok"] is False
    assert any("unverified" in str(p).lower() for p in (result_bad.get("problems") or []))


def test_render_pdf_page_roundtrip() -> None:
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument.new()
        doc.new_page(width=200, height=200)
        buf = io.BytesIO()
        doc.save(buf)
        doc.close()
        pdf_bytes = buf.getvalue()
    except Exception:
        pytest.skip("pypdfium2 PdfDocument.new not available")
    png = render_pdf_page(pdf_bytes, 0, dpi=72)
    img = Image.open(io.BytesIO(png))
    assert img.size == (200, 200)


def test_decks_yaml_lists_five_issuers() -> None:
    from scripts.fetch_decks import load_decks

    decks = load_decks()
    keys = {d["issuer"] for d in decks}
    assert keys == {"duke_corp", "intel", "verizon", "amazon", "boeing"}
    assert all(d["url"].startswith("http") for d in decks)
