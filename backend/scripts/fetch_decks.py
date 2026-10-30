"""Download investor presentation decks into Filing rows (form_type=presentation).

Usage (from backend/):
    python -m scripts.fetch_decks
    python -m scripts.fetch_decks --issuer boeing

Uses NewsroomClient (robots.txt, rate limit, SEC_USER_AGENT). Idempotent on URL.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.ingest.pdf import document_bytes_to_text, looks_like_pdf
from app.models import Filing, Issuer
from app.sources.newsroom import NewsroomClient, NewsroomError

DEFAULT_DECKS = Path(__file__).resolve().parents[1] / "seed" / "decks.yaml"
FORM_PRESENTATION = "presentation"


def _load_dotenv_candidates() -> None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / ".env", here.parents[2] / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_decks(path: Path | str = DEFAULT_DECKS) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return list(data.get("decks") or [])


def upsert_deck(
    session: Session,
    client: NewsroomClient,
    deck: dict[str, Any],
) -> Filing:
    issuer_key = str(deck["issuer"])
    url = str(deck["url"])
    issuer = session.scalars(select(Issuer).where(Issuer.key == issuer_key)).first()
    if issuer is None:
        raise NewsroomError(f"unknown issuer key {issuer_key!r} — load seed first")

    published = date.fromisoformat(str(deck["published"]))
    title = str(deck.get("title") or f"{issuer_key} presentation")

    row = session.scalars(select(Filing).where(Filing.url == url)).first()
    if row is None:
        row = Filing(url=url)
        session.add(row)

    data, content_type = client.fetch_bytes(url)
    if not looks_like_pdf(data, content_type=content_type, url=url):
        raise NewsroomError(f"deck URL did not return a PDF: {url}")
    text = document_bytes_to_text(data, content_type=content_type, url=url)

    row.issuer_id = issuer.id
    row.form_type = FORM_PRESENTATION
    row.filed_at = published
    row.title = title
    row.url = url
    row.content_bytes = data
    row.body_text = text
    row.content_hash = hashlib.sha256(data).hexdigest()
    session.flush()
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decks", type=Path, default=DEFAULT_DECKS)
    parser.add_argument("--issuer", type=str, default=None, help="Only this issuer key")
    args = parser.parse_args(argv)

    _load_dotenv_candidates()
    get_settings.cache_clear()

    decks = load_decks(args.decks)
    if args.issuer:
        decks = [d for d in decks if str(d.get("issuer")) == args.issuer]
    if not decks:
        print("No decks to fetch.", file=sys.stderr)
        return 1

    try:
        with NewsroomClient() as client, SessionLocal() as session:
            for deck in decks:
                try:
                    row = upsert_deck(session, client, deck)
                    session.commit()
                    n = len(row.content_bytes or b"")
                    print(
                        f"{deck['issuer']}: {row.id}  {n // 1024} KiB  "
                        f"text={len(row.body_text or '')} chars  {row.title}"
                    )
                except Exception as exc:  # noqa: BLE001 — continue other decks
                    session.rollback()
                    print(f"{deck.get('issuer')}: ERROR {exc}", file=sys.stderr)
    except NewsroomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
