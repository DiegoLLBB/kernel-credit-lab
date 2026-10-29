"""Load a year-end capital-structure snapshot into Postgres.

Usage (from backend/):
    python -m scripts.load_seed seed/snapshot_2025-12-31.json

Validates the whole file first and refuses to write on any error. Idempotent:
issuers match by CIK, instruments by key or CUSIP; existing rows are updated.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CallTerms, CuratedSource, Instrument, Issuer, MaturitySchedule, Watchlist

CURATED_FIELDS = (
    "coupon",
    "maturity",
    "issue_date",
    "amount_outstanding",
    "cusip",
    "seniority",
    "security",
)


class SeedValidationError(ValueError):
    """Raised when the seed file fails validation; nothing is written."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        joined = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Seed validation failed ({len(errors)} error(s)):\n{joined}")


@dataclass
class LoadStats:
    issuers_created: int = 0
    issuers_updated: int = 0
    instruments_created: int = 0
    instruments_updated: int = 0
    watchlist_created: int = 0
    call_terms_upserted: int = 0
    curated_sources_upserted: int = 0
    maturity_schedules_created: int = 0
    maturity_schedules_updated: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_date(value: str | None, *, field_name: str, context: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SeedValidationError([f"{context}: invalid {field_name} {value!r} ({exc})"]) from exc


def _parse_decimal(value: Any, *, field_name: str, context: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise SeedValidationError([f"{context}: invalid {field_name} {value!r}"]) from exc


def _field_value(raw: dict[str, Any], name: str) -> str | None:
    value = raw.get(name)
    if value is None or value == "":
        return None
    return str(value)


def validate_seed(data: dict[str, Any]) -> None:
    """Validate the snapshot; raise SeedValidationError with every problem found."""
    errors: list[str] = []

    sources = data.get("sources")
    issuers = data.get("issuers")
    instruments = data.get("instruments")
    watchlist = data.get("watchlist")
    maturity_schedules = data.get("maturity_schedules", [])

    if not isinstance(sources, dict) or not sources:
        errors.append("sources: must be a non-empty object keyed by source id")
        sources = {}
    if not isinstance(issuers, list) or not issuers:
        errors.append("issuers: must be a non-empty list")
        issuers = []
    if not isinstance(instruments, list) or not instruments:
        errors.append("instruments: must be a non-empty list")
        instruments = []
    if not isinstance(watchlist, list):
        errors.append("watchlist: must be a list of issuer keys")
        watchlist = []
    if maturity_schedules is None:
        maturity_schedules = []
    if not isinstance(maturity_schedules, list):
        errors.append("maturity_schedules: must be a list")
        maturity_schedules = []

    issuer_keys: set[str] = set()
    issuer_ciks: set[str] = set()
    for idx, issuer in enumerate(issuers):
        ctx = f"issuers[{idx}]"
        if not isinstance(issuer, dict):
            errors.append(f"{ctx}: must be an object")
            continue
        key = issuer.get("key")
        cik = issuer.get("cik")
        name = issuer.get("name")
        if not key:
            errors.append(f"{ctx}: missing key")
        elif key in issuer_keys:
            errors.append(f"{ctx}: duplicate issuer key {key!r}")
        else:
            issuer_keys.add(str(key))
        if not name:
            errors.append(f"{ctx}: missing name")
        if not cik:
            errors.append(f"{ctx}: missing cik")
        elif str(cik) in issuer_ciks:
            errors.append(f"{ctx}: duplicate cik {cik!r}")
        else:
            issuer_ciks.add(str(cik))

    for idx, issuer in enumerate(issuers):
        if not isinstance(issuer, dict):
            continue
        parent = issuer.get("parent")
        if parent is not None and parent not in issuer_keys:
            errors.append(f"issuers[{idx}] ({issuer.get('key')}): parent {parent!r} not in issuers")

    for wkey in watchlist:
        if wkey not in issuer_keys:
            errors.append(f"watchlist: unknown issuer key {wkey!r}")

    instrument_keys: set[str] = set()
    cusips: set[str] = set()
    for idx, inst in enumerate(instruments):
        ctx = f"instruments[{idx}]"
        if not isinstance(inst, dict):
            errors.append(f"{ctx}: must be an object")
            continue
        key = inst.get("key")
        ikey = f"{ctx} ({key})" if key else ctx
        if not key:
            errors.append(f"{ctx}: missing key")
        elif key in instrument_keys:
            errors.append(f"{ctx}: duplicate instrument key {key!r}")
        else:
            instrument_keys.add(str(key))

        issuer_ref = inst.get("issuer")
        if not issuer_ref:
            errors.append(f"{ikey}: missing issuer")
        elif issuer_ref not in issuer_keys:
            errors.append(f"{ikey}: issuer {issuer_ref!r} not in issuers")

        src_ids = inst.get("sources") or []
        if not isinstance(src_ids, list) or not src_ids:
            errors.append(f"{ikey}: sources must be a non-empty list")
        else:
            for sid in src_ids:
                if sid not in sources:
                    errors.append(f"{ikey}: unknown source {sid!r}")

        try:
            amount = _parse_decimal(
                inst.get("amount_outstanding"),
                field_name="amount_outstanding",
                context=ikey,
            )
            if amount <= 0:
                errors.append(f"{ikey}: amount_outstanding must be > 0 (got {amount})")
        except SeedValidationError as exc:
            errors.extend(exc.errors)

        try:
            _parse_decimal(inst.get("coupon"), field_name="coupon", context=ikey)
        except SeedValidationError as exc:
            errors.extend(exc.errors)

        maturity = None
        issue_date = None
        try:
            maturity = _parse_date(inst.get("maturity"), field_name="maturity", context=ikey)
            if maturity is None:
                errors.append(f"{ikey}: missing maturity")
            issue_date = _parse_date(inst.get("issue_date"), field_name="issue_date", context=ikey)
        except SeedValidationError as exc:
            errors.extend(exc.errors)

        if issue_date is not None and maturity is not None and issue_date >= maturity:
            errors.append(
                f"{ikey}: issue_date {issue_date.isoformat()} must be before "
                f"maturity {maturity.isoformat()}"
            )

        rate_type = inst.get("rate_type")
        if rate_type not in {"fixed", "floating"}:
            errors.append(f"{ikey}: rate_type must be 'fixed' or 'floating' (got {rate_type!r})")
        if (inst.get("reference_rate") is not None or inst.get("spread_bps") is not None) and (
            rate_type != "floating"
        ):
            errors.append(
                f"{ikey}: floating-rate fields set but rate_type is {rate_type!r} "
                "(floating-rate instruments must be flagged rate_type=floating)"
            )

        cusip = inst.get("cusip")
        if cusip:
            if cusip in cusips:
                errors.append(f"{ikey}: duplicate cusip {cusip!r}")
            else:
                cusips.add(str(cusip))

        if not inst.get("name"):
            errors.append(f"{ikey}: missing name")

        currency = inst.get("currency")
        if (
            currency is not None
            and currency != ""
            and (not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha())
        ):
            errors.append(f"{ikey}: currency must be a 3-letter ISO 4217 code (got {currency!r})")

        as_of = inst.get("as_of")
        if as_of:
            try:
                _parse_date(as_of, field_name="as_of", context=ikey)
            except SeedValidationError as exc:
                errors.extend(exc.errors)

        call_terms = inst.get("call_terms")
        if call_terms is not None:
            if not isinstance(call_terms, dict):
                errors.append(f"{ikey}: call_terms must be an object or null")
            else:
                try:
                    _parse_date(
                        call_terms.get("par_call_date"),
                        field_name="par_call_date",
                        context=ikey,
                    )
                except SeedValidationError as exc:
                    errors.extend(exc.errors)

    schedule_keys: set[tuple[str, str]] = set()
    for idx, sched in enumerate(maturity_schedules):
        ctx = f"maturity_schedules[{idx}]"
        if not isinstance(sched, dict):
            errors.append(f"{ctx}: must be an object")
            continue
        issuer_ref = sched.get("issuer")
        skey = f"{ctx} ({issuer_ref})" if issuer_ref else ctx
        if not issuer_ref:
            errors.append(f"{ctx}: missing issuer")
        elif issuer_ref not in issuer_keys:
            errors.append(f"{skey}: issuer {issuer_ref!r} not in issuers")

        basis = sched.get("basis")
        if basis not in {"consolidated", "registrant"}:
            errors.append(f"{skey}: basis must be 'consolidated' or 'registrant' (got {basis!r})")

        as_of = None
        try:
            as_of = _parse_date(sched.get("as_of"), field_name="as_of", context=skey)
            if as_of is None:
                errors.append(f"{skey}: missing as_of")
        except SeedValidationError as exc:
            errors.extend(exc.errors)

        if issuer_ref and as_of is not None:
            pair = (str(issuer_ref), as_of.isoformat())
            if pair in schedule_keys:
                errors.append(f"{skey}: duplicate schedule for issuer/as_of {pair}")
            else:
                schedule_keys.add(pair)

        source_id = sched.get("source")
        if not source_id:
            errors.append(f"{skey}: missing source")
        elif source_id not in sources:
            errors.append(f"{skey}: unknown source {source_id!r}")

        buckets = sched.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            errors.append(f"{skey}: buckets must be a non-empty list")
            continue
        for bidx, bucket in enumerate(buckets):
            bctx = f"{skey}.buckets[{bidx}]"
            if not isinstance(bucket, dict):
                errors.append(f"{bctx}: must be an object")
                continue
            if not bucket.get("label"):
                errors.append(f"{bctx}: missing label")
            try:
                amount = _parse_decimal(bucket.get("amount"), field_name="amount", context=bctx)
                if amount < 0:
                    errors.append(f"{bctx}: amount must be >= 0 (got {amount})")
            except SeedValidationError as exc:
                errors.extend(exc.errors)
            try:
                _parse_date(bucket.get("period_end"), field_name="period_end", context=bctx)
            except SeedValidationError as exc:
                errors.extend(exc.errors)

    if errors:
        raise SeedValidationError(errors)


def _source_urls_and_note(inst: dict[str, Any], sources: dict[str, Any]) -> tuple[str, str | None]:
    urls: list[str] = []
    titles: list[str] = []
    for sid in inst.get("sources") or []:
        src = sources[sid]
        urls.append(str(src["url"]))
        if src.get("title"):
            titles.append(str(src["title"]))
    note_parts = [p for p in (inst.get("note"), "; ".join(titles) if titles else None) if p]
    return " | ".join(urls), (" | ".join(note_parts) if note_parts else None)


def _upsert_curated_sources(
    session: Session,
    instrument: Instrument,
    raw: dict[str, Any],
    sources: dict[str, Any],
    stats: LoadStats,
) -> None:
    source_url, note = _source_urls_and_note(raw, sources)
    as_of = _parse_date(raw.get("as_of"), field_name="as_of", context=raw.get("key", "?"))
    existing = {
        cs.field: cs
        for cs in session.scalars(
            select(CuratedSource).where(CuratedSource.instrument_id == instrument.id)
        )
    }
    for field_name in CURATED_FIELDS:
        value = _field_value(raw, field_name)
        if value is None:
            continue
        row = existing.get(field_name)
        if row is None:
            row = CuratedSource(instrument_id=instrument.id, field=field_name)
            session.add(row)
        row.value = value
        row.source_url = source_url
        row.as_of = as_of
        row.note = note
        stats.curated_sources_upserted += 1


def load_seed(session: Session, path: Path | str) -> LoadStats:
    """Validate and load a snapshot JSON file. Raises SeedValidationError on bad input."""
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SeedValidationError(["root: seed file must be a JSON object"])

    validate_seed(data)
    stats = LoadStats()
    sources: dict[str, Any] = data["sources"]
    issuers_raw: list[dict[str, Any]] = data["issuers"]
    instruments_raw: list[dict[str, Any]] = data["instruments"]
    watchlist_raw: list[str] = data["watchlist"]
    schedules_raw: list[dict[str, Any]] = data.get("maturity_schedules") or []

    # --- Issuers (by CIK), then parent links ---
    by_key: dict[str, Issuer] = {}
    for raw in issuers_raw:
        cik = str(raw["cik"])
        issuer = session.scalars(select(Issuer).where(Issuer.cik == cik)).first()
        if issuer is None:
            issuer = Issuer(cik=cik)
            session.add(issuer)
            stats.issuers_created += 1
        else:
            stats.issuers_updated += 1
        issuer.key = str(raw["key"])
        issuer.name = str(raw["name"])
        issuer.ticker = raw.get("ticker")
        issuer.sector = raw.get("sector")
        by_key[str(raw["key"])] = issuer

    session.flush()

    for raw in issuers_raw:
        issuer = by_key[str(raw["key"])]
        parent_key = raw.get("parent")
        issuer.parent_id = by_key[parent_key].id if parent_key else None

    session.flush()

    # --- Watchlist ---
    for wkey in watchlist_raw:
        issuer = by_key[wkey]
        existing = session.scalars(
            select(Watchlist).where(Watchlist.issuer_id == issuer.id)
        ).first()
        if existing is None:
            session.add(Watchlist(issuer_id=issuer.id, label=wkey))
            stats.watchlist_created += 1

    # --- Instruments ---
    for raw in instruments_raw:
        key = str(raw["key"])
        cusip = raw.get("cusip")
        instrument = session.scalars(select(Instrument).where(Instrument.key == key)).first()
        if instrument is None and cusip:
            instrument = session.scalars(
                select(Instrument).where(Instrument.cusip == str(cusip))
            ).first()
        if instrument is None:
            instrument = Instrument(
                key=key,
                issuer_id=by_key[str(raw["issuer"])].id,
                name=str(raw["name"]),
                coupon=_parse_decimal(raw["coupon"], field_name="coupon", context=key),
                maturity=_parse_date(raw["maturity"], field_name="maturity", context=key),  # type: ignore[arg-type]
                amount_outstanding=_parse_decimal(
                    raw["amount_outstanding"],
                    field_name="amount_outstanding",
                    context=key,
                ),
            )
            session.add(instrument)
            stats.instruments_created += 1
        else:
            stats.instruments_updated += 1
            instrument.key = key
            instrument.issuer_id = by_key[str(raw["issuer"])].id
            instrument.name = str(raw["name"])

        instrument.instrument_type = str(raw.get("instrument_type") or "bond")
        instrument.coupon = _parse_decimal(raw["coupon"], field_name="coupon", context=key)
        instrument.rate_type = str(raw["rate_type"])
        instrument.reference_rate = raw.get("reference_rate")
        instrument.spread_bps = raw.get("spread_bps")
        instrument.maturity = _parse_date(raw["maturity"], field_name="maturity", context=key)  # type: ignore[assignment]
        instrument.issue_date = _parse_date(
            raw.get("issue_date"), field_name="issue_date", context=key
        )
        instrument.amount_outstanding = _parse_decimal(
            raw["amount_outstanding"], field_name="amount_outstanding", context=key
        )
        instrument.currency = str(raw.get("currency") or "USD").upper()
        instrument.seniority = raw.get("seniority")
        instrument.security = raw.get("security")
        instrument.cusip = str(cusip) if cusip else None
        instrument.status = str(raw.get("status") or "active")

        session.flush()

        call_raw = raw.get("call_terms")
        if call_raw:
            terms = instrument.call_terms
            if terms is None:
                terms = CallTerms(instrument_id=instrument.id)
                session.add(terms)
            terms.par_call_date = _parse_date(
                call_raw.get("par_call_date"),
                field_name="par_call_date",
                context=key,
            )
            terms.make_whole_spread_bps = call_raw.get("make_whole_spread_bps")
            stats.call_terms_upserted += 1

        _upsert_curated_sources(session, instrument, raw, sources, stats)

    # --- Maturity schedules (unique per issuer + as_of) ---
    for raw in schedules_raw:
        issuer = by_key[str(raw["issuer"])]
        as_of = _parse_date(raw["as_of"], field_name="as_of", context=raw["issuer"])
        assert as_of is not None
        source = sources[str(raw["source"])]
        buckets = [
            {
                "label": str(b["label"]),
                "period_end": b.get("period_end"),
                "amount": str(b["amount"]),
            }
            for b in raw["buckets"]
        ]
        row = session.scalars(
            select(MaturitySchedule).where(
                MaturitySchedule.issuer_id == issuer.id,
                MaturitySchedule.as_of == as_of,
            )
        ).first()
        if row is None:
            row = MaturitySchedule(issuer_id=issuer.id, as_of=as_of)
            session.add(row)
            stats.maturity_schedules_created += 1
        else:
            stats.maturity_schedules_updated += 1
        row.basis = str(raw["basis"])
        row.source_url = str(source["url"])
        row.buckets = buckets

    session.flush()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        help="Path to snapshot JSON (e.g. seed/snapshot_2025-12-31.json)",
    )
    args = parser.parse_args(argv)
    if not args.path.is_file():
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 2

    started = datetime.now()
    try:
        with SessionLocal() as session:
            stats = load_seed(session, args.path)
            session.commit()
    except SeedValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Seed validation failed: invalid JSON ({exc})", file=sys.stderr)
        return 1

    elapsed = (datetime.now() - started).total_seconds()
    print(
        f"Loaded {args.path}: "
        f"issuers +{stats.issuers_created}/~{stats.issuers_updated}, "
        f"instruments +{stats.instruments_created}/~{stats.instruments_updated}, "
        f"watchlist +{stats.watchlist_created}, "
        f"call_terms {stats.call_terms_upserted}, "
        f"curated_sources {stats.curated_sources_upserted}, "
        f"maturity_schedules +{stats.maturity_schedules_created}"
        f"/~{stats.maturity_schedules_updated} "
        f"({elapsed:.2f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
