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
