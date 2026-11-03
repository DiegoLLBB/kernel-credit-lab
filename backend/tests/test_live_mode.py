"""Live mode helpers: freeze row, YoY same-period, review writes gate, score sentence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base
from app.eval.summary import build_eval_summary
from app.ingest.live import DEFAULT_LIVE_SINCE, _replay_key_for_identity, ensure_live_config
from app.metrics.periods import anniversary, prior_same_period
from app.models import LiveConfig, ReplayEvaluation


def test_default_live_since_is_27_oct_2026():
    assert date(2026, 10, 27) == DEFAULT_LIVE_SINCE


def test_anniversary_and_same_period_prior():
    assert anniversary(date(2026, 9, 30)) == date(2025, 9, 30)
    latest = SimpleNamespace(period_end=date(2026, 9, 30))
    series = [
        SimpleNamespace(period_end=date(2024, 12, 31)),
        SimpleNamespace(period_end=date(2025, 9, 30)),
        SimpleNamespace(period_end=date(2025, 12, 31)),
        latest,
    ]
    prior = prior_same_period(series, latest)
    assert prior is not None
    assert prior.period_end == date(2025, 9, 30)
    # Far-only history → no prior (never jump two years back)
    far = [
        SimpleNamespace(period_end=date(2024, 12, 31)),
        latest,
    ]
    assert prior_same_period(far, latest) is None


def test_replay_key_stable_for_accession_and_url():
    assert _replay_key_for_identity("0001193125-26-123456") == "live:edgar:0001193125-26-123456"
    a = _replay_key_for_identity("https://example.com/a")
    b = _replay_key_for_identity("https://example.com/a")
    c = _replay_key_for_identity("https://example.com/b")
    assert a == b and a.startswith("live:url:") and a != c


def test_ensure_live_config_freezes_once():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    first = ensure_live_config(db, prompt_version="event_v10")
    db.commit()
    second = ensure_live_config(db, prompt_version="event_v99")
    assert first.id == second.id
    assert second.prompt_version == "event_v10"
    assert db.scalar(select(LiveConfig)) is not None
    db.close()
    engine.dispose()


def test_eval_summary_uses_alert_counts():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    row = ReplayEvaluation(
        precision=Decimal("0.90000"),
        recall=Decimal("0.81818"),
        incomplete=False,
        per_event=[
            {
                "replay_key": "miss_one",
                "event_date": "2026-03-01",
                "expected_alert": True,
                "got": "logged",
                "miss": True,
            },
            {
                "replay_key": "false_one",
                "event_date": "2026-04-01",
                "expected_alert": False,
                "got": "alerted",
                "miss": True,
            },
            {
                "replay_key": "hit",
                "event_date": "2026-05-01",
                "expected_alert": True,
                "got": "alerted",
                "miss": False,
            },
        ],
        alert_counts={
            "true_positive": 9,
            "false_positive": 1,
            "true_negative": 0,
            "false_negative": 5,  # 2 logged misses + 3 paused/failed expected alerts
            "incomplete_expected_alert": 2,
            "failed_expected_alert": 1,
        },
    )
    db.add(row)
    db.commit()
    summary = build_eval_summary(db, row)
    assert summary is not None
    assert summary["caught"] == 9
    assert summary["expected_material"] == 14
    assert summary["false_alerts"] == 1
    assert summary["text"] == "Caught 9 of 14 material changes, 1 false alert"
    assert abs(summary["precision"] - 0.9) < 1e-9
    assert abs(summary["recall"] - (9 / 14)) < 1e-9
    assert any(e["name"] == "miss_one" for e in summary["missed_events"])
    assert any(e["name"] == "false_one" for e in summary["false_events"])
    db.close()
    engine.dispose()


def test_eval_summary_folds_legacy_parked_misses(monkeypatch):
    """Legacy rows with incomplete outside FN recompute so Y includes them."""
    from app.eval import summary as summary_mod
    from app.eval.replay_eval import AlertConfusion, EvalReport

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    row = ReplayEvaluation(
        precision=Decimal("0.90000"),
        recall=Decimal("0.81818"),
        incomplete=False,
        per_event=[],
        alert_counts={
            "true_positive": 9,
            "false_positive": 1,
            "true_negative": 0,
            "false_negative": 2,
            "incomplete_expected_alert": 3,
            "failed_expected_alert": 1,
        },
    )
    db.add(row)
    db.commit()

    recomputed = EvalReport()
    recomputed.alert = AlertConfusion(
        true_positive=9,
        false_positive=1,
        true_negative=0,
        false_negative=6,
        incomplete_expected_alert=3,
        failed_expected_alert=1,
    )
    monkeypatch.setattr(summary_mod, "evaluate_latest", lambda session: recomputed)
    summary = build_eval_summary(db, row)
    assert summary is not None
    assert summary["expected_material"] == 15
    assert summary["text"] == "Caught 9 of 15 material changes, 1 false alert"
    db.close()
    engine.dispose()


def test_review_writes_setting_defaults_true():
    get_settings.cache_clear()
    assert Settings().review_writes_enabled is True
    get_settings.cache_clear()


def test_review_writes_setting_can_disable(monkeypatch):
    monkeypatch.setenv("REVIEW_WRITES_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().review_writes_enabled is False
    get_settings.cache_clear()


def test_pending_live_events_one_resume_then_skip():
    """Paused runs get one resume; needs_input / exhausted pauses stay out."""
    from datetime import date as date_cls

    from app.ingest.live import (
        live_resume_attempts,
        mark_live_resume_attempt,
        pending_live_events,
    )
    from app.models import Event, Issuer
    from app.models.trace import AgentRun

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    issuer = Issuer(key="boeing", name="Boeing", ticker="BA")
    db.add(issuer)
    db.flush()

    def _event(key: str, status: str = "processing") -> Event:
        ev = Event(
            issuer_id=issuer.id,
            event_type="10-Q",
            event_date=date_cls(2026, 10, 27),
            source="live",
            status=status,
            replay_key=key,
            summary=key,
        )
        db.add(ev)
        db.flush()
        return ev

    # Fresh pause: one session → still resumable.
    e1 = _event("live:edgar:pause1")
    r1 = AgentRun(
        event_id=e1.id,
        question="q",
        workflow="event_monitor",
        status="paused",
        result={"sessions": [{"started_at": "t0", "ended_at": "t1"}]},
    )
    db.add(r1)
    db.flush()
    e1.run_id = r1.id

    # Already resumed once (2 sessions) → skip.
    e2 = _event("live:edgar:pause2")
    r2 = AgentRun(
        event_id=e2.id,
        question="q",
        workflow="event_monitor",
        status="paused",
        result={
            "sessions": [
                {"started_at": "t0", "ended_at": "t1"},
                {"started_at": "t2", "ended_at": "t3"},
            ]
        },
    )
    db.add(r2)
    db.flush()
    e2.run_id = r2.id

    # needs_input must never re-queue.
    e3 = _event("live:edgar:needs")
    r3 = AgentRun(
        event_id=e3.id,
        question="q",
        workflow="event_monitor",
        status="needs_input",
        result={"status": "needs_input"},
    )
    db.add(r3)
    db.flush()
    e3.run_id = r3.id

    # Pending with no run → include.
    e4 = _event("live:edgar:pending", status="pending")

    db.commit()

    pending_ids = {e.id for e in pending_live_events(db)}
    assert e1.id in pending_ids
    assert e2.id not in pending_ids
    assert e3.id not in pending_ids
    assert e4.id in pending_ids

    assert live_resume_attempts(r1) == 0
    assert live_resume_attempts(r2) == 1
    assert mark_live_resume_attempt(r1) == 1
    db.commit()
    assert e1.id not in {e.id for e in pending_live_events(db)}

    db.close()
    engine.dispose()


def test_bank_structured_notes_are_not_monitored_and_stats_cover_all_events():
    """Bank 424B2/424B3 pricing supplements stay out of the queue, list and counts;
    counts cover every live event, not just the newest slice."""
    from datetime import date as date_cls

    from app.ingest.live import (
        is_bank_structured_note,
        live_stats,
        pending_live_events,
    )
    from app.models import Alert, Event, Issuer
    from app.models.trace import AgentRun

    assert is_bank_structured_note("Financials", "424B2")
    assert is_bank_structured_note("Banks", "424b3")
    assert not is_bank_structured_note("Financials", "10-Q")
    assert not is_bank_structured_note("Information Technology", "424B2")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    bank = Issuer(key="jpm", name="JPMorgan Chase & Co.", ticker="JPM", sector="Financials")
    corp = Issuer(
        key="amazon", name="Amazon.com, Inc.", ticker="AMZN", sector="Consumer Discretionary"
    )
    db.add_all([bank, corp])
    db.flush()

    def _event(issuer: Issuer, key: str, form: str, day: int, status: str = "pending") -> Event:
        ev = Event(
            issuer_id=issuer.id,
            event_type=form,
            event_date=date_cls(2026, 11, day),
            source="live",
            status=status,
            replay_key=key,
            summary=key,
        )
        db.add(ev)
        db.flush()
        return ev

    # An older live alert that would fall outside a 40-row slice.
    old = _event(corp, "live:amzn:10q", "10-Q", 1, status="processed")
    run = AgentRun(event_id=old.id, question="q", workflow="event_monitor", status="alerted")
    db.add(run)
    db.flush()
    old.run_id = run.id
    db.add(
        Alert(event_id=old.id, issuer_id=corp.id, run_id=run.id, title="Term loan", status="new")
    )

    notes = [_event(bank, f"live:jpm:note{i}", "424B2", 3) for i in range(45)]
    bank_10q = _event(bank, "live:jpm:10q", "10-Q", 3)
    corp_8k = _event(corp, "live:amzn:8k", "8-K", 2)
    db.commit()

    queued = {e.id for e in pending_live_events(db)}
    assert not queued & {n.id for n in notes}
    assert bank_10q.id in queued and corp_8k.id in queued

    stats = live_stats(db)
    assert stats["alerted"] == 1  # still counted although newer rows exceed the list cap
    assert stats["documents_read"] == 1  # pending documents are not "read"
    listed = {d["event_id"] for d in stats["documents"]}
    assert not listed & {n.id for n in notes}
    assert old.id in listed

    db.close()
    engine.dispose()


def test_brief_facts_name_live_forms_in_words(monkeypatch):
    """Form codes such as 424B2 must not reach the brief writer: its number gate reads
    them as unsupported numbers and drops the sentence."""
    from datetime import date as date_cls

    import app.insight.brief as brief

    fake = {
        "go_live_at": "2026-11-02T12:00:00+00:00",
        "alerted": 0,
        "logged": 1,
        "refused": 0,
        "paused": 1,
        "failed": 0,
        "documents_read": 2,
        "documents": [
            {
                "issuer_key": "jpm",
                "issuer_name": "JPMorgan",
                "form": "424B2",
                "event_date": "2026-11-03",
                "outcome": "reviewed, no alert",
            },
            {
                "issuer_key": "boeing",
                "issuer_name": "Boeing",
                "form": "10-Q",
                "event_date": "2026-10-28",
                "outcome": "paused",
            },
        ],
    }
    monkeypatch.setattr(brief, "live_stats", lambda session: fake)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    facts = brief.build_brief_facts(db, date_cls(2026, 11, 3))
    forms = [d["form"] for d in facts["live"]["documents"]]
    assert forms == ["prospectus supplement (bond offering)", "quarterly report"]
    assert "424B" not in str(facts)
    db.close()
    engine.dispose()


def test_stale_maturity_schedule_rule():
    from datetime import date as date_cls

    from app.insight.facts import schedule_is_stale

    today = date_cls(2026, 11, 3)
    assert schedule_is_stale("2012-12-31", today)  # GM's last tagged schedule
    assert schedule_is_stale("2022-12-31", today)  # JPMorgan's
    assert not schedule_is_stale("2025-05-31", today)  # Oracle's FY2025 10-K
    assert not schedule_is_stale("2025-12-31", today)
    assert not schedule_is_stale(None, today)
    assert not schedule_is_stale("not a date", today)
