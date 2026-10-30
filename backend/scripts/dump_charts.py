"""Export stored chart readings (crop, overlay, values, verification) for review.

    python -m scripts.dump_charts                    # all issuers
    python -m scripts.dump_charts --issuer verizon

Writes to replay_runs/charts/<issuer>/ (gitignored): p<page>_<id>_crop.png,
p<page>_<id>_overlay.png and readings.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ChartReading, Issuer
from app.replay.runner import DEFAULT_RUNS_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export chart readings for review.")
    parser.add_argument("--issuer", help="issuer key (default: all)")
    args = parser.parse_args(argv)

    out_root = Path(DEFAULT_RUNS_DIR) / "charts"
    with SessionLocal() as session:
        query = select(ChartReading, Issuer.key).join(Issuer, Issuer.id == ChartReading.issuer_id)
        if args.issuer:
            query = query.where(Issuer.key == args.issuer)
        rows = session.execute(query.order_by(Issuer.key, ChartReading.page)).all()
        summary: dict[str, list[dict]] = {}
        for reading, key in rows:
            out = out_root / key
            out.mkdir(parents=True, exist_ok=True)
            stem = f"p{reading.page + 1:03d}_{reading.id}"
            if reading.crop_png:
                (out / f"{stem}_crop.png").write_bytes(reading.crop_png)
            if reading.overlay_png:
                (out / f"{stem}_overlay.png").write_bytes(reading.overlay_png)
            summary.setdefault(key, []).append(
                {
                    "id": reading.id,
                    "page": reading.page + 1,
                    "title": reading.title,
                    "unit": reading.unit,
                    "bbox": reading.bbox,
                    "categories": reading.categories,
                    "values": reading.values,
                    "verified": reading.verified,
                    "verification_detail": reading.verification_detail,
                }
            )
        for key, items in summary.items():
            (out_root / key / "readings.json").write_text(
                json.dumps(items, indent=2, default=str)
            )
            print(f"{key}: {len(items)} readings -> {out_root / key}")
    if not rows:
        print("no chart readings stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
