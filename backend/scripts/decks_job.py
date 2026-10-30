"""Cloud decks job: fetch investor decks, then run the SAM chart reader.

Runs ``fetch_decks`` (all issuers) followed by ``process_decks`` (all pages),
printing one line per deck/chart plus a final summary. Exits nonzero on failure
so the Cloud Run execution shows as failed. Local usage is unchanged.

Usage (from backend/):
    python -m scripts.decks_job
    python -m scripts.decks_job --max-pages 10
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime

from scripts import fetch_decks, process_decks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--issuer", default=None, help="Only this issuer key")
    args = parser.parse_args(argv)

    started = datetime.now()
    try:
        print("decks: step 1/2 fetch_decks")
        fetch_argv: list[str] = []
        if args.issuer:
            fetch_argv += ["--issuer", args.issuer]
        fetch_rc = fetch_decks.main(fetch_argv)
        if fetch_rc != 0:
            print(f"decks: FAILED in fetch_decks (exit {fetch_rc})", file=sys.stderr)
            return fetch_rc

        print("decks: step 2/2 process_decks")
        process_argv: list[str] = []
        if args.max_pages is not None:
            process_argv += ["--max-pages", str(args.max_pages)]
        if args.issuer:
            process_argv += ["--issuer", args.issuer]
        process_rc = process_decks.main(process_argv)
        if process_rc != 0:
            print(f"decks: FAILED in process_decks (exit {process_rc})", file=sys.stderr)
            return process_rc

        elapsed = (datetime.now() - started).total_seconds()
        print(f"decks: complete in {elapsed:.1f}s (cost printed by process_decks above)")
    except Exception as exc:
        traceback.print_exc()
        print(f"decks: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
