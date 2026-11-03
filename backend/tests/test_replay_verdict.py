"""Batched / stored feed verdicts for overview badges."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.eval.replay_verdict import (
    FEED_VERDICTS_KEY,
    attach_feed_verdicts,
    build_feed_verdicts,
    overview_feed_verdicts,
    stored_feed_verdicts,
)
from app.models import Alert, Event, Issuer, ReplayExpectation
from app.models.credit import ReplayEvaluation


def _db() -> tuple[Session, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _expectation(**kwargs: object) -> ReplayExpectation:
    defaults: dict[str, object] = {
        "issuer_key": "acme",
        "event_type": "8-K",
        "event_date": date(2026, 5, 1),
        "available_from": date(2026, 5, 1),
        "title": "test",
        "sources": [],
        "confirming_sources": [],
        "expected_changes": [],
        "expected_alert": False,
    }
    defaults.update(kwargs)
    return ReplayExpectation(**defaults)  # type: ignore[arg-type]


def test_build_and_store_feed_verdicts() -> None:
    db, engine = _db()
    issuer = Issuer(key="acme", name="Acme", ticker="ACM")
    db.add(issuer)
    db.flush()

    live_ev = Event(
        issuer_id=issuer.id,
        event_date=date(2026, 10, 1),
        event_type="8-K",
        status="processed",
        source="live",
    )
    hit_ev = Event(
        issuer_id=issuer.id,
        event_date=date(2026, 5, 1),
        event_type="8-K",
        status="processed",
        source="replay",
        replay_key="hit",
    )
    false_ev = Event(
        issuer_id=issuer.id,
        event_date=date(2026, 4, 1),
        event_type="8-K",
        status="processed",
        source="replay",
        replay_key="false_one",
    )
    db.add_all([live_ev, hit_ev, false_ev])
    db.flush()

    db.add_all(
        [
            _expectation(
                key="hit",
                event_id=hit_ev.id,
                expected_alert=True,
                event_date=date(2026, 5, 1),
            ),
            _expectation(
                key="false_one",
                event_id=false_ev.id,
                expected_alert=False,
                event_date=date(2026, 4, 1),
            ),
        ]
    )
    live_alert = Alert(
        issuer_id=issuer.id,
        event_id=live_ev.id,
        title="Live",
        summary="live",
        kind="agent",
        status="new",
    )
    hit_alert = Alert(
        issuer_id=issuer.id,
        event_id=hit_ev.id,
        title="Hit",
        summary="hit",
        kind="agent",
        status="new",
    )
    false_alert = Alert(
        issuer_id=issuer.id,
        event_id=false_ev.id,
        title="False",
        summary="false",
        kind="agent",
        status="new",
    )
    db.add_all([live_alert, hit_alert, false_alert])
    db.flush()

    row = ReplayEvaluation(
        kind="agent",
        per_event=[
            {
                "replay_key": "hit",
                "event_date": "2026-05-01",
                "expected_alert": True,
                "got": "alerted",
                "miss": False,
            },
            {
                "replay_key": "false_one",
                "event_date": "2026-04-01",
                "expected_alert": False,
                "got": "alerted",
                "miss": True,
            },
        ],
        alert_counts={"true_positive": 1, "false_positive": 1},
    )
    db.add(row)
    db.flush()

    attach_feed_verdicts(db, row)
    db.commit()

    stored = stored_feed_verdicts(row)
    assert stored[live_alert.id]["verdict"] == "not yet scored"
    assert stored[hit_alert.id]["verdict"] == "correct"
    assert stored[false_alert.id]["verdict"] == "false alert"
    assert FEED_VERDICTS_KEY in (row.alert_counts or {})

    again = overview_feed_verdicts(db, [live_alert, hit_alert, false_alert])
    assert again[hit_alert.id]["verdict"] == "correct"
    assert again[false_alert.id]["verdict"] == "false alert"

    built = build_feed_verdicts(db, alerts=[hit_alert], eval_row=row)
    assert built[hit_alert.id]["verdict"] == "correct"

    db.close()
    engine.dispose()
