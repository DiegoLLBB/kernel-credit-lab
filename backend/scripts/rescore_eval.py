"""Rescore agent + baseline ReplayEvaluation rows (no model calls).

Usage (from backend/):
    python -m scripts.rescore_eval
"""

from __future__ import annotations

import json
import sys

from app.db import SessionLocal
from app.eval.rescore import build_comparison, rescore_all


def main(argv: list[str] | None = None) -> int:
    del argv
    with SessionLocal() as session:
        try:
            result = rescore_all(session)
        except Exception as exc:
            print(f"rescore: FAILED: {exc}", file=sys.stderr)
            raise
        comparison = build_comparison(session)
        session.commit()

    agent = result["agent"]
    baseline = result["baseline"]
    print(
        f"rescore: Kernel caught with right year and amount: "
        f"{agent['accurate_caught']} of {agent['accurate_total']}"
    )
    print(f"rescore: Kernel wall/end-state: {agent['wall_passed']} of {agent['wall_total']}")
    print(
        f"rescore: baseline caught with right year and amount: "
        f"{baseline['accurate_caught']} of {baseline['accurate_total']}"
    )
    print("rescore: baseline keeps records: — (one-shot model stores no maturity wall)")
    print("rescore: comparison table")
    print(json.dumps(comparison, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
