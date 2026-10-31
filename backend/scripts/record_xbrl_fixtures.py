"""Record byte-for-byte SEC companyfacts fixtures (opt-in live fetch).

Usage:
    XBRL_LIVE=1 python scripts/record_xbrl_fixtures.py

Without ``XBRL_LIVE=1`` this script refuses to run so CI stays offline. With
it set, it fetches each target CIK's companyfacts document with the standard
Kernel SEC client and writes the response **bytes unchanged** to
``tests/fixtures/xbrl/CIK##########.json``. Fixtures are never hand-edited;
re-record (and update the README with the new source/date) when they go stale.

Conventions mirror scripts/record_edgar_fixtures.py and
scripts/record_fixtures.py: list target URLs first, honor SEC rate limits,
skip unchanged bodies without rewriting, print a short summary.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from app.sources.edgar import _require_user_agent
    from app.sources.xbrl import XbrlClient, pad_cik, xbrl_url
except ImportError:  # direct execution: `python scripts/record_xbrl_fixtures.py`
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from app.sources.edgar import _require_user_agent  # noqa: E402
    from app.sources.xbrl import XbrlClient, pad_cik, xbrl_url  # noqa: E402

# (CIK, human label). Includes Duke Energy Progress (facts filer) plus the two
# Duke subsidiaries that return tiny non-facts payloads (used to lock in the
# missing-with-reason path).
TARGETS = [
    ("0001326160", "Duke Energy (DUK)"),
    ("0000017797", "Duke Energy Progress"),
    ("0000030371", "Duke Energy Carolinas (no us-gaap facts)"),
    ("0000037637", "Duke Energy Florida (no fact sections)"),
    ("0001341439", "Oracle (ORCL)"),
    ("0000732712", "Verizon (VZ)"),
    ("0000012927", "Boeing (BA)"),
    ("0000050863", "Intel (INTC)"),
    ("0001018724", "Amazon (AMZN)"),
]


def main() -> int:
    if os.environ.get("XBRL_LIVE") != "1":
        print("Refusing to hit the live SEC API without XBRL_LIVE=1 (offline by default).")
        return 2
    user_agent = os.environ.get("KERNEL_SEC_USER_AGENT", "")
    _require_user_agent(user_agent)
    out_dir = ROOT / "tests" / "fixtures" / "xbrl"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = XbrlClient(user_agent=user_agent)
    print(f"Recording {len(TARGETS)} companyfacts fixtures -> {out_dir}")
    for cik, label in TARGETS:
        url = xbrl_url(pad_cik(cik))
        body = client.fetch_body(cik)
        path = out_dir / f"CIK{pad_cik(cik)}.json"
        digest = hashlib.sha256(body).hexdigest()[:12]
        existing = path.read_bytes() if path.exists() else None
        if existing == body:
            print(f"  unchanged {path.name} ({label}) sha256:{digest} ({len(body)} bytes)")
        else:
            path.write_bytes(body)
            status = "updated" if existing is not None else "recorded"
            print(f"  {status} {path.name} ({label}) sha256:{digest}")
            print(f"    ({len(body)} bytes) from {url}")
    print("Done. Update tests/fixtures/xbrl/README.md with the recording date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
