"""ReplayEvaluation: --eval-latest persists a DB row; the API prefers it over files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.replay import _latest_eval_summary
from app.db import Base
from app.eval.replay_eval import (
    AlertConfusion,
    EvalReport,
    EventCostRow,
    store_latest_evaluation,
)
from app.models import ReplayEvaluation


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


def _report(**overrides) -> EvalReport:
    alert = AlertConfusion(true_positive=2, false_positive=0, true_negative=1)
    params: dict = {
        "alert": alert,
        "per_event": [
            EventCostRow(
                replay_key="vz_redemption_notice",
                event_date="2026-08-20",
                status="alerted",
                cost_usd=0.25,
                tokens=1000,
                turns=19,
                coverage_score=1.0,
                expected_alert=True,
                got="alerted",
            )
        ],
        "totals": {"expectations": 1, "events": 1, "cost_usd": 0.25},
    }
    params.update(overrides)
    return EvalReport(**params)


def test_store_latest_evaluation_writes_row(session: Session) -> None:
    row = store_latest_evaluation(session, _report())
    session.commit()

    assert session.scalar(select(ReplayEvaluation)) is not None
    assert row.precision == Decimal("1")
    assert row.recall == Decimal("1")
    assert row.incomplete is False
    assert row.cost_usd == Decimal("0.25")
    assert row.event_count == 1
    assert row.expectation_count == 1
    assert row.per_event[0]["replay_key"] == "vz_redemption_notice"
    assert row.prompt_version == "event_v10"
    assert row.git_sha is None or len(row.git_sha) == 40  # None where git is unavailable
    assert row.evaluated_at is not None


def test_latest_eval_prefers_database_row(session: Session, tmp_path: Path) -> None:
    store_latest_evaluation(session, _report())
    session.commit()
    stale = tmp_path / "latest_eval.json"
    stale.write_text(
        json.dumps(
            {
                "incomplete": True,
                "alert": {"precision": 0.1, "recall": 0.1},
                "totals": {"cost_usd": 99.0},
                "evaluated_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    summary = _latest_eval_summary(session, tmp_path)

    assert summary is not None
    assert summary.precision == 1.0
    assert summary.recall == 1.0
    assert summary.incomplete is False
    assert summary.cost_usd == 0.25
    assert summary.source_path is not None and summary.source_path.startswith("replay_evaluation:")


def test_latest_eval_uses_newest_row(session: Session, tmp_path: Path) -> None:
    old = store_latest_evaluation(session, _report())
    old.evaluated_at = datetime(2026, 1, 1, tzinfo=UTC)
    new = store_latest_evaluation(
        session,
        _report(totals={"expectations": 2, "events": 2, "cost_usd": 0.5}),
    )
    new.evaluated_at = datetime(2026, 2, 1, tzinfo=UTC)
    session.commit()

    summary = _latest_eval_summary(session, tmp_path)

    assert summary is not None
    assert summary.cost_usd == 0.5
    assert summary.source_path == f"replay_evaluation:{new.id}"


def test_latest_eval_falls_back_to_file(session: Session, tmp_path: Path) -> None:
    latest = tmp_path / "latest_eval.json"
    latest.write_text(
        json.dumps(
            {
                "incomplete": False,
                "alert": {"precision": 0.8, "recall": 0.75},
                "totals": {"cost_usd": 12.5},
                "evaluated_at": "2026-10-30T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    summary = _latest_eval_summary(session, tmp_path)

    assert summary is not None
    assert summary.precision == 0.8
    assert summary.recall == 0.75
    assert summary.cost_usd == 12.5
    assert summary.source_path is not None and summary.source_path.endswith("latest_eval.json")


def test_latest_eval_empty_without_row_or_file(session: Session, tmp_path: Path) -> None:
    assert _latest_eval_summary(session, tmp_path) is None
