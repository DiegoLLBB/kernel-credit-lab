"""Run the SAM chart reader over stored issuer presentations.

    python -m scripts.process_decks                     # every presentation Filing
    python -m scripts.process_decks --issuer duke_corp --max-pages 10
    python -m scripts.process_decks --reprocess         # drop earlier readings for those decks

Each page is rendered, SAM 3.1 finds bar charts and bars, Muse Spark reads the values and the
bar geometry verifies them (``app/charts/pipeline.py``). Commits after every page so an
interrupted run keeps its work; prints one line per chart and a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select

from app.charts.pipeline import (
    DEFAULT_DPI,
    DEFAULT_MAX_PAGES,
    pdf_page_count,
    process_page_charts,
    render_pdf_page,
)
from app.config import get_settings
from app.db import SessionLocal
from app.llm import LLMClient
from app.models import ChartReading, Filing, Issuer


def _cost(row: ChartReading) -> float:
    llm = (row.verification_detail or {}).get("llm") or {}
    return float(llm.get("cost_usd") or 0)


def _line(row: ChartReading) -> str:
    detail = row.verification_detail or {}
    title = (row.title or "(untitled)")[:60]
    if row.verified:
        return f"  p{row.page + 1:>3}  verified    {title}"
    why = detail.get("reason") or detail.get("status") or "unverified"
    return f"  p{row.page + 1:>3}  UNVERIFIED  {title}  ({str(why)[:80]})"


async def _run(issuer_key: str | None, max_pages: int, dpi: int, reprocess: bool) -> int:
    with SessionLocal() as session:
        query = select(Filing).where(Filing.form_type == "presentation")
        if issuer_key:
            issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
            if issuer is None:
                print(f"unknown issuer key {issuer_key!r}", file=sys.stderr)
                return 2
            query = query.where(Filing.issuer_id == issuer.id)
        filings = session.scalars(query.order_by(Filing.filed_at)).all()
        if not filings:
            print("no presentation filings; run scripts.fetch_decks first", file=sys.stderr)
            return 1

        charts = verified = 0
        cost = 0.0
        async with LLMClient(get_settings()) as client:
            for filing in filings:
                existing = session.scalar(
                    select(func.count())
                    .select_from(ChartReading)
                    .where(ChartReading.filing_id == filing.id)
                )
                if existing and not reprocess:
                    print(f"{filing.title}: {existing} readings exist (--reprocess to redo)")
                    continue
                if existing:
                    session.execute(delete(ChartReading).where(ChartReading.filing_id == filing.id))
                    session.commit()
                if not filing.content_bytes:
                    print(f"{filing.title}: no PDF bytes stored, skipped")
                    continue

                n_pages = min(pdf_page_count(filing.content_bytes), max_pages)
                print(f"{filing.title}: {n_pages} pages", flush=True)
                deck_charts = deck_verified = 0
                for page_i in range(n_pages):
                    try:
                        png = render_pdf_page(filing.content_bytes, page_i, dpi=dpi)
                        rows = await process_page_charts(
                            session, filing, png, page_i, client=client
                        )
                        session.commit()
                    except Exception as exc:  # keep going; one bad page must not end the run
                        session.rollback()
                        print(f"  p{page_i + 1:>3}  ERROR  {type(exc).__name__}: {exc}"[:160])
                        continue
                    for row in rows:
                        print(_line(row), flush=True)
                        cost += _cost(row)
                    deck_charts += len(rows)
                    deck_verified += sum(1 for r in rows if r.verified)
                print(f"  -> {deck_charts} chart(s), {deck_verified} verified", flush=True)
                charts += deck_charts
                verified += deck_verified

    print("---")
    print(f"decks     {len(filings)}")
    print(f"charts    {charts}")
    print(f"verified  {verified}")
    print(f"cost_usd  {cost:.4f}  (Spark reads recorded on the readings)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SAM chart reader over decks.")
    parser.add_argument("--issuer", help="issuer key, e.g. duke_corp (default: all)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--reprocess", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.issuer, args.max_pages, args.dpi, args.reprocess))


if __name__ == "__main__":
    raise SystemExit(main())
