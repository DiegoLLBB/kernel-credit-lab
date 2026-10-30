"""Evaluate a replay run against ``ReplayExpectation`` and end-state checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.replay import DEFAULT_REPLAY_PATH
from app.models import Alert, Event, Instrument, ReplayExpectation, StateChange
from app.models.trace import AgentRun, ModelCall, ToolCall
from app.tools.maturity import instrument_state_at

INCOMPLETE_STATUSES = frozenset({"needs_input", "paused"})
FAILED_STATUSES = frozenset({"failed"})
HIDDEN_RUN_STATUSES = frozenset({"superseded", "abandoned"})
AMOUNT_TOLERANCE = Decimal("0.01")  # 1%


def _coverage_score_value(raw: Any) -> float | None:
    """``AgentRun.coverage_score`` is a JSON object with a ``score`` field."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return float(raw)
    if isinstance(raw, dict) and raw.get("score") is not None:
        try:
            return float(raw["score"])
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class AlertConfusion:
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    incomplete_expected_alert: int = 0
    incomplete_expected_no_alert: int = 0
    failed_expected_alert: int = 0
    failed_expected_no_alert: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.true_positive + self.false_positive
        if denom == 0:
            return None
        return self.true_positive / denom

    @property
    def recall(self) -> float | None:
        denom = self.true_positive + self.false_negative
        if denom == 0:
            return None
        return self.true_positive / denom

    @property
    def failed_total(self) -> int:
        return self.failed_expected_alert + self.failed_expected_no_alert

    @property
    def incomplete_total(self) -> int:
        return self.incomplete_expected_alert + self.incomplete_expected_no_alert

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
            "incomplete_expected_alert": self.incomplete_expected_alert,
            "incomplete_expected_no_alert": self.incomplete_expected_no_alert,
            "failed_expected_alert": self.failed_expected_alert,
            "failed_expected_no_alert": self.failed_expected_no_alert,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass
class ChangeMatch:
    expectation_key: str
    expected: dict[str, Any]
    matched: bool
    detail: str
    actual: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation_key": self.expectation_key,
            "expected": self.expected,
            "matched": self.matched,
            "detail": self.detail,
            "actual": self.actual,
        }


@dataclass
class EndStateResult:
    as_of: str
    source: str | None
    check: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "source": self.source,
            "check": self.check,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class EventCostRow:
    replay_key: str | None
    event_date: str
    status: str
    cost_usd: float
    tokens: int
    turns: int
    coverage_score: float | None
    expected_alert: bool | None = None
    got: str | None = None
    miss: bool = False
    last_tool: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_key": self.replay_key,
            "event_date": self.event_date,
            "status": self.status,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "turns": self.turns,
            "coverage_score": self.coverage_score,
            "expected_alert": self.expected_alert,
            "got": self.got,
            "miss": self.miss,
            "last_tool": self.last_tool,
        }


@dataclass
class EvalReport:
    alert: AlertConfusion = field(default_factory=AlertConfusion)
    changes: list[ChangeMatch] = field(default_factory=list)
    end_state: list[EndStateResult] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    per_event: list[EventCostRow] = field(default_factory=list)
    variance: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    incomplete: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incomplete": self.incomplete,
            "error": self.error,
            "alert": self.alert.to_dict(),
            "changes": [c.to_dict() for c in self.changes],
            "end_state": [e.to_dict() for e in self.end_state],
            "coverage": self.coverage,
            "per_event": [r.to_dict() for r in self.per_event],
            "variance": self.variance,
            "totals": self.totals,
        }


def _got_label(alerted: bool | None, status: str | None) -> str:
    if alerted is True:
        return "alerted"
    if alerted is False:
        return "logged"
    if status:
        return str(status)
    return "missing"


def _last_tool_call(session: Session, run: AgentRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    row = session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at.desc())
    ).first()
    if row is None:
        return None
    return {
        "tool_name": row.tool_name,
        "arguments": row.arguments,
        "result": row.result,
        "status": row.status,
        "error": row.error,
    }


def load_end_state_checks(path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(path) if path is not None else DEFAULT_REPLAY_PATH
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return list(data.get("end_state_checks") or [])


def _amount_within_tol(
    actual: Decimal, expected: Decimal, *, tol: Decimal = AMOUNT_TOLERANCE
) -> bool:
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / abs(expected) <= tol


def _parse_money(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    text = str(raw).replace(",", "").replace("$", "").strip()
    # Strip tolerance annotations like "(±1m)"
    text = re.sub(r"\(.*\)", "", text).strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _date_matches(actual: date | None, expected_raw: Any) -> tuple[bool, str]:
    if expected_raw is None:
        return True, "no expected date"
    if actual is None:
        return False, "actual date missing"
    text = str(expected_raw).strip()
    if len(text) == 7 and text[4] == "-":  # YYYY-MM
        return (
            actual.year == int(text[:4]) and actual.month == int(text[5:7]),
            f"month precision {text} vs {actual.isoformat()}",
        )
    try:
        expected = date.fromisoformat(text[:10])
    except ValueError:
        return False, f"unparseable expected date {text!r}"
    return actual == expected, f"{actual.isoformat()} vs {expected.isoformat()}"


def _applied_changes_for_event(session: Session, event: Event) -> list[StateChange]:
    return list(
        session.scalars(
            select(StateChange).where(
                StateChange.event_id == event.id, StateChange.status == "applied"
            )
        ).all()
    )


def _match_change(
    session: Session,
    expectation_key: str,
    expected: dict[str, Any],
    applied: list[StateChange],
) -> ChangeMatch:
    action = expected.get("action")
    if action == "log_only":
        return ChangeMatch(
            expectation_key=expectation_key,
            expected=expected,
            matched=True,
            detail="log_only (no state change required)",
        )

    instrument_key = expected.get("instrument_key")
    candidates = []
    for change in applied:
        diff = change.proposed_diff or {}
        if diff.get("action") != action:
            continue
        if instrument_key and diff.get("instrument_key") != instrument_key:
            # Also match via instrument row.
            if change.instrument_id:
                inst = session.get(Instrument, change.instrument_id)
                if inst is None or inst.key != instrument_key:
                    continue
            else:
                continue
        candidates.append(change)

    if not candidates:
        return ChangeMatch(
            expectation_key=expectation_key,
            expected=expected,
            matched=False,
            detail=f"no applied change with action={action!r} instrument={instrument_key!r}",
        )

    change = candidates[0]
    diff = change.proposed_diff or {}
    problems: list[str] = []

    # Amount checks
    expected_amount = None
    for key in ("new_amount", "amount", "by", "amount_outstanding"):
        if key in expected and expected[key] is not None:
            expected_amount = _parse_money(expected[key])
            break
    if expected_amount is not None:
        actual_amount = None
        if action == "reduce_outstanding":
            # Prefer after / new outstanding; fall back to reduce_by.
            if diff.get("after") is not None and "new_amount" in expected:
                actual_amount = _parse_money(diff.get("after"))
            elif expected.get("by") is not None:
                before = _parse_money(diff.get("before"))
                after = _parse_money(diff.get("after"))
                if before is not None and after is not None:
                    actual_amount = before - after
                    expected_amount = _parse_money(expected.get("by"))
                else:
                    actual_amount = _parse_money(diff.get("reduce_by") or diff.get("after"))
            else:
                actual_amount = _parse_money(diff.get("after"))
        elif action == "add_instrument":
            actual_amount = _parse_money(diff.get("amount_outstanding"))
        else:
            actual_amount = _parse_money(diff.get("after") or diff.get("amount_outstanding"))
        if actual_amount is None:
            problems.append("actual amount missing")
        elif expected_amount is not None and not _amount_within_tol(actual_amount, expected_amount):
            problems.append(f"amount {actual_amount} outside 1% of expected {expected_amount}")

    # Date checks
    date_raw = expected.get("effective") or expected.get("effective_date")
    if date_raw is not None:
        ok, detail = _date_matches(change.effective_date, date_raw)
        if not ok:
            problems.append(f"date mismatch ({detail})")

    # Maturity year for add_instrument
    if action == "add_instrument" and expected.get("maturity_year") is not None:
        mat = diff.get("maturity")
        if not mat or not str(mat).startswith(str(expected["maturity_year"])):
            problems.append(f"maturity year {expected['maturity_year']} not in actual {mat!r}")

    matched = not problems
    return ChangeMatch(
        expectation_key=expectation_key,
        expected=expected,
        matched=matched,
        detail="; ".join(problems) if problems else "matched",
        actual={
            "proposed_diff": diff,
            "effective_date": (
                change.effective_date.isoformat() if change.effective_date else None
            ),
        },
    )


_ABSENT_RE = re.compile(r"^(\S+)\s+absent$", re.I)
_EQ_RE = re.compile(
    r"^(\S+)\s*=\s*([0-9,]+(?:\.\d+)?)\s*(?:\(([^)]+)\))?$",
    re.I,
)


def _eval_end_state_check(
    session: Session, as_of: date, source: str | None, check: str
) -> EndStateResult:
    text = check.strip()
    m_abs = _ABSENT_RE.match(text)
    if m_abs:
        key = m_abs.group(1)
        inst = session.scalars(select(Instrument).where(Instrument.key == key)).first()
        if inst is None:
            return EndStateResult(
                as_of=as_of.isoformat(),
                source=source,
                check=check,
                passed=True,
                detail="instrument row absent",
            )
        _amt, status = instrument_state_at(session, inst, as_of)
        passed = status not in {"active"}
        return EndStateResult(
            as_of=as_of.isoformat(),
            source=source,
            check=check,
            passed=passed,
            detail=f"status={status} at {as_of.isoformat()}",
        )

    m_eq = _EQ_RE.match(text)
    if m_eq:
        key, amount_s, tol_note = m_eq.group(1), m_eq.group(2), m_eq.group(3)
        expected = _parse_money(amount_s)
        inst = session.scalars(select(Instrument).where(Instrument.key == key)).first()
        if inst is None:
            return EndStateResult(
                as_of=as_of.isoformat(),
                source=source,
                check=check,
                passed=False,
                detail="instrument not found",
            )
        amount, status = instrument_state_at(session, inst, as_of)
        if status != "active":
            return EndStateResult(
                as_of=as_of.isoformat(),
                source=source,
                check=check,
                passed=False,
                detail=f"status={status}, amount={amount}",
            )
        # Optional absolute tolerance like ±1m
        tol = AMOUNT_TOLERANCE
        abs_tol: Decimal | None = None
        if tol_note:
            note = tol_note.lower().replace("±", "").replace("+/-", "").strip()
            if note.endswith("m"):
                try:
                    abs_tol = Decimal(note[:-1]) * Decimal("1000000")
                except InvalidOperation:
                    abs_tol = None
        if expected is None:
            passed = False
            detail = "could not parse expected amount"
        elif abs_tol is not None:
            passed = abs(amount - expected) <= abs_tol
            detail = f"amount={amount} expected={expected} abs_tol={abs_tol}"
        else:
            passed = _amount_within_tol(amount, expected, tol=tol)
            detail = f"amount={amount} expected={expected} status={status}"
        return EndStateResult(
            as_of=as_of.isoformat(),
            source=source,
            check=check,
            passed=passed,
            detail=detail,
        )

    return EndStateResult(
        as_of=as_of.isoformat(),
        source=source,
        check=check,
        passed=False,
        detail=f"unrecognized check syntax: {check!r}",
    )


def _event_failed(
    event: Event | None, run: AgentRun | None, log_row: dict[str, Any] | None
) -> bool:
    if run is not None and run.status in FAILED_STATUSES:
        return True
    if event is not None and event.status in FAILED_STATUSES:
        return True
    return bool(log_row is not None and str(log_row.get("status") or "") in FAILED_STATUSES)


def _event_alerted_material(session: Session, event: Event, run: AgentRun | None) -> bool | None:
    """True if material alert filed; False if logged; None if incomplete/failed."""
    if run is None:
        if event.status == "processed":
            return False
        return None
    if run.status in FAILED_STATUSES:
        return None
    if run.status in INCOMPLETE_STATUSES or run.status == "running":
        return None
    if run.status == "alerted":
        return True
    if run.status == "logged":
        return False
    alert = session.scalars(select(Alert).where(Alert.event_id == event.id)).first()
    return alert is not None


def _latest_run_for_event(session: Session, event: Event) -> AgentRun | None:
    """Latest non-superseded / non-abandoned run for ``event`` (prefers ``event.run_id``)."""
    if event.run_id:
        run = session.get(AgentRun, event.run_id)
        if run is not None and (run.status or "") not in HIDDEN_RUN_STATUSES:
            return run
    return session.scalars(
        select(AgentRun)
        .where(
            AgentRun.event_id == event.id,
            AgentRun.status.not_in(sorted(HIDDEN_RUN_STATUSES)),
        )
        .order_by(AgentRun.created_at.desc())
    ).first()


def _visible_runs_for_event(session: Session, event: Event) -> list[AgentRun]:
    """Non-superseded / non-abandoned runs for ``event``, oldest first."""
    return list(
        session.scalars(
            select(AgentRun)
            .where(
                AgentRun.event_id == event.id,
                AgentRun.status.not_in(sorted(HIDDEN_RUN_STATUSES)),
            )
            .order_by(AgentRun.created_at.asc())
        ).all()
    )


def _score_alert_outcome(
    report: EvalReport,
    *,
    expected_alert: bool,
    alerted: bool | None,
    failed: bool,
) -> None:
    if failed:
        if expected_alert:
            report.alert.failed_expected_alert += 1
        else:
            report.alert.failed_expected_no_alert += 1
    elif alerted is None:
        if expected_alert:
            report.alert.incomplete_expected_alert += 1
        else:
            report.alert.incomplete_expected_no_alert += 1
    elif expected_alert and alerted:
        report.alert.true_positive += 1
    elif expected_alert and not alerted:
        report.alert.false_negative += 1
    elif not expected_alert and alerted:
        report.alert.false_positive += 1
    else:
        report.alert.true_negative += 1


def evaluate_replay(
    session: Session,
    *,
    replay_path: Path | str | None = None,
    log: dict[str, Any] | None = None,
    end_state_as_of: date | None = None,
    only_keys: set[str] | None = None,
    prefer_latest_run: bool = False,
) -> EvalReport:
    """Compare agent outcomes with ``ReplayExpectation`` and end-state checks.

    Skipped ``--only`` events (still pending / logged as skipped) are excluded.
    When ``prefer_latest_run`` is True (``--eval-latest``), each expectation uses the
    latest non-superseded AgentRun for its linked event.

    Merged filing events (several expectations → one Event): alert confusion counts
    once per event. ``expected_alert`` is true if *any* linked expectation expects an
    alert, so a sibling ``log`` expectation is not scored as a false positive when the
    event alerted. Cost/tokens are attributed once per event. ``totals.events`` is the
    expectation count (not calendar/rate extras).
    """
    report = EvalReport()
    if log:
        report.incomplete = bool(log.get("incomplete"))
        report.error = log.get("error")
        if only_keys is None and log.get("only_keys"):
            only_keys = {str(k) for k in log["only_keys"]}

    expectations = session.scalars(
        select(ReplayExpectation).order_by(ReplayExpectation.available_from, ReplayExpectation.key)
    ).all()

    siblings_by_event: dict[str, list[ReplayExpectation]] = {}
    for exp in expectations:
        if exp.event_id:
            siblings_by_event.setdefault(exp.event_id, []).append(exp)

    log_by_key: dict[str, dict[str, Any]] = {}
    skipped_keys: set[str] = set()
    if log:
        for row in log.get("events") or []:
            key = row.get("replay_key")
            if not key:
                continue
            key_s = str(key)
            log_by_key[key_s] = row
            if row.get("skipped") or row.get("status") == "skipped":
                skipped_keys.add(key_s)
        # Merged filing events: map every linked expectation key to the same log row.
        for exp in expectations:
            if exp.key in log_by_key or not exp.event_id:
                continue
            event = session.get(Event, exp.event_id)
            if event is None or not event.replay_key:
                continue
            if event.replay_key in log_by_key:
                log_by_key[exp.key] = log_by_key[event.replay_key]
                if event.replay_key in skipped_keys:
                    skipped_keys.add(exp.key)

    scored_alert_groups: set[str] = set()
    cost_groups: set[str] = set()
    variance_events: set[str] = set()
    scored_event_ids: set[str] = set()
    expectation_rows = 0
    total_cost = 0.0
    total_tokens = 0
    total_turns = 0

    for exp in expectations:
        if only_keys is not None and exp.key not in only_keys:
            continue
        if exp.key in skipped_keys:
            continue

        event = None
        if exp.event_id:
            event = session.get(Event, exp.event_id)
        if event is None and exp.key:
            event = session.scalars(select(Event).where(Event.replay_key == exp.key)).first()

        log_row = log_by_key.get(exp.key)
        run: AgentRun | None = None
        if event is not None:
            if prefer_latest_run or not log_row:
                run = _latest_run_for_event(session, event)
            elif event.run_id:
                run = session.get(AgentRun, event.run_id)

        alerted = _event_alerted_material(session, event, run) if event else None
        own_expected_alert = bool(exp.expected_alert)
        if exp.event_id and exp.event_id in siblings_by_event:
            group_expected_alert = any(
                bool(s.expected_alert) for s in siblings_by_event[exp.event_id]
            )
            score_key = f"event:{exp.event_id}"
        else:
            group_expected_alert = own_expected_alert
            score_key = f"key:{exp.key}"

        failed = _event_failed(event, run, log_row)
        status = (
            (run.status if run else None)
            or (str(log_row["status"]) if log_row and log_row.get("status") else None)
            or (event.status if event else None)
            or "missing"
        )
        got = _got_label(alerted, status)
        # Sibling log of an expected-alert merge is not a miss when the event alerted.
        if alerted is not None and (not own_expected_alert and group_expected_alert and alerted):
            miss = False
        else:
            miss = alerted is not None and (alerted != own_expected_alert)

        if score_key not in scored_alert_groups:
            scored_alert_groups.add(score_key)
            _score_alert_outcome(
                report,
                expected_alert=group_expected_alert,
                alerted=alerted,
                failed=failed,
            )

        if event is not None:
            scored_event_ids.add(event.id)
            applied = _applied_changes_for_event(session, event)
            for change in exp.expected_changes or []:
                if not isinstance(change, dict):
                    continue
                report.changes.append(_match_change(session, exp.key, change, applied))

            if event.id not in variance_events:
                variance_events.add(event.id)
                runs = _visible_runs_for_event(session, event)
                if len(runs) > 1:
                    report.variance.append(
                        {
                            "event_id": event.id,
                            "replay_key": event.replay_key,
                            "expectation_keys": [
                                s.key for s in siblings_by_event.get(event.id, [exp])
                            ],
                            "runs": [
                                {
                                    "run_id": r.id,
                                    "status": r.status,
                                    "created_at": (
                                        r.created_at.isoformat() if r.created_at else None
                                    ),
                                    "material": (r.result or {}).get("material"),
                                    "got": _got_label(
                                        _event_alerted_material(session, event, r),
                                        r.status,
                                    ),
                                }
                                for r in runs
                            ],
                        }
                    )

        coverage = _coverage_score_value(run.coverage_score if run else None)
        report.coverage.append(
            {
                "key": exp.key,
                "coverage_score": coverage,
                "status": status,
            }
        )

        tokens = 0
        cost = 0.0
        turns = 0
        if run is not None:
            cost = float(run.total_cost_usd or 0)
            turns = int(run.turn_count or 0)
            for mc in session.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all():
                tokens += int(mc.input_tokens or 0) + int(mc.output_tokens or 0)
        elif log_row:
            cost = float(log_row.get("cost_usd") or 0)
            tokens = int(log_row.get("tokens") or 0)
            turns = int(log_row.get("turns") or 0)

        # Attribute cost once per merged event (first expectation in iteration order).
        if score_key not in cost_groups:
            cost_groups.add(score_key)
            total_cost += cost
            total_tokens += tokens
            total_turns += turns
            row_cost, row_tokens, row_turns = cost, tokens, turns
        else:
            row_cost, row_tokens, row_turns = 0.0, 0, 0

        expectation_rows += 1
        report.per_event.append(
            EventCostRow(
                replay_key=exp.key,
                event_date=(
                    event.event_date.isoformat() if event else exp.available_from.isoformat()
                ),
                status=status,
                cost_usd=row_cost,
                tokens=row_tokens,
                turns=row_turns,
                coverage_score=coverage,
                expected_alert=own_expected_alert,
                got=got,
                miss=miss,
                last_tool=_last_tool_call(session, run) if miss else None,
            )
        )

    # Calendar / rate events — include when not skipped and (no only filter).
    # Cost rows only; never scored as alerts; excluded from totals.events.
    # Skip events already covered by a ReplayExpectation (incl. merged filing: keys).
    if only_keys is None:
        seen = {r.replay_key for r in report.per_event}
        extra_events = session.scalars(
            select(Event).where(Event.replay_key.is_not(None)).order_by(Event.event_date)
        ).all()
        for event in extra_events:
            if event.id in scored_event_ids:
                continue
            if event.replay_key in seen or event.replay_key in skipped_keys:
                continue
            rk = str(event.replay_key or "")
            if rk.startswith("rate_update:") and event.status != "processed":
                continue
            run = session.get(AgentRun, event.run_id) if event.run_id else None
            tokens = 0
            cost = 0.0
            turns = 0
            status = event.status
            coverage = None
            if run is not None:
                status = run.status
                cost = float(run.total_cost_usd or 0)
                turns = int(run.turn_count or 0)
                coverage = _coverage_score_value(run.coverage_score)
                for mc in session.scalars(
                    select(ModelCall).where(ModelCall.run_id == run.id)
                ).all():
                    tokens += int(mc.input_tokens or 0) + int(mc.output_tokens or 0)
            total_cost += cost
            total_tokens += tokens
            total_turns += turns
            report.per_event.append(
                EventCostRow(
                    replay_key=event.replay_key,
                    event_date=event.event_date.isoformat(),
                    status=status,
                    cost_usd=cost,
                    tokens=tokens,
                    turns=turns,
                    coverage_score=coverage,
                    expected_alert=None,
                    got=status,
                    miss=False,
                    last_tool=None,
                )
            )

    # End-state checks only on a full (non --only) evaluation.
    if only_keys is None:
        checks = load_end_state_checks(replay_path)
        for block in checks:
            as_of_s = block.get("as_of")
            if not as_of_s:
                continue
            as_of = date.fromisoformat(str(as_of_s))
            if end_state_as_of is not None and as_of != end_state_as_of:
                continue
            source = block.get("source")
            for check in block.get("checks") or []:
                report.end_state.append(_eval_end_state_check(session, as_of, source, str(check)))

    report.totals = {
        "events": expectation_rows,
        "cost_usd": total_cost,
        "tokens": total_tokens,
        "turns": total_turns,
        "changes_matched": sum(1 for c in report.changes if c.matched),
        "changes_total": len(report.changes),
        "end_state_passed": sum(1 for e in report.end_state if e.passed),
        "end_state_total": len(report.end_state),
        "mean_coverage": (
            sum(c["coverage_score"] for c in report.coverage if c["coverage_score"] is not None)
            / max(
                1,
                sum(1 for c in report.coverage if c["coverage_score"] is not None),
            )
            if any(c["coverage_score"] is not None for c in report.coverage)
            else None
        ),
    }
    return report


def render_eval_markdown(report: EvalReport) -> str:
    a = report.alert
    prec = f"{a.precision:.3f}" if a.precision is not None else "n/a"
    rec = f"{a.recall:.3f}" if a.recall is not None else "n/a"
    lines = [
        "# Replay evaluation",
        "",
    ]
    if report.incomplete or report.error:
        lines.append("## Run status")
        lines.append("")
        lines.append(f"- Incomplete: **{report.incomplete}**")
        if report.error:
            lines.append(f"- Error: `{report.error}`")
        lines.append("")
    lines.extend(
        [
            "## Alert precision / recall",
            "",
            f"- Precision: **{prec}** (TP / (TP+FP))",
            f"- Recall: **{rec}** (TP / (TP+FN))",
            f"- Incomplete (needs_input / paused): {a.incomplete_total} (not counted as misses)",
            f"- Failed (runner error): {a.failed_total} (not counted as misses)",
            "",
            "### Confusion table",
            "",
            "| | Expected alert | Expected no alert |",
            "|---|---:|---:|",
            f"| Alerted (material) | {a.true_positive} | {a.false_positive} |",
            f"| Logged / not material | {a.false_negative} | {a.true_negative} |",
            f"| Incomplete | {a.incomplete_expected_alert} | {a.incomplete_expected_no_alert} |",
            f"| Failed | {a.failed_expected_alert} | {a.failed_expected_no_alert} |",
            "",
            "## Per-event outcomes",
            "",
            "| Event | Expected | Got | Status | Miss |",
            "|---|---|---|---|---|",
        ]
    )
    for r in report.per_event:
        if r.expected_alert is None:
            continue
        exp_s = "alert" if r.expected_alert else "log"
        miss_s = "yes" if r.miss else "no"
        lines.append(
            f"| {r.replay_key or '—'} | {exp_s} | {r.got or '—'} | {r.status} | {miss_s} |"
        )
    lines.append("")
    misses = [r for r in report.per_event if r.miss]
    if misses:
        lines.extend(["### Miss detail (last tool call)", ""])
        for r in misses:
            expected = "alert" if r.expected_alert else "log"
            lines.append(f"**{r.replay_key}** — expected {expected}, got {r.got}")
            tool = r.last_tool
            if tool:
                args_s = json.dumps(tool.get("arguments"), default=str)[:400]
                result_s = json.dumps(tool.get("result"), default=str)[:600]
                lines.append(f"- Tool: `{tool.get('tool_name')}` ({tool.get('status')})")
                lines.append(f"- Arguments: `{args_s}`")
                lines.append(f"- Result: `{result_s}`")
            else:
                lines.append("- No tool call recorded.")
            lines.append("")

    lines.extend(
        [
            "## Expected changes",
            "",
        ]
    )
    if not report.changes:
        lines.append("_No expected changes evaluated._")
        lines.append("")
    else:
        lines.append("| Event | Action | Matched | Detail |")
        lines.append("|---|---|---|---|")
        for c in report.changes:
            action = c.expected.get("action")
            lines.append(
                f"| {c.expectation_key} | {action} | {'yes' if c.matched else 'no'} | {c.detail} |"
            )
        lines.append("")

    lines.extend(["## End-state checks (30 Jun)", ""])
    if not report.end_state:
        lines.append("_No end-state checks._")
        lines.append("")
    else:
        lines.append("| As of | Check | Passed | Detail |")
        lines.append("|---|---|---|---|")
        for e in report.end_state:
            lines.append(
                f"| {e.as_of} | `{e.check}` | {'yes' if e.passed else 'no'} | {e.detail} |"
            )
        lines.append("")

    lines.extend(["## Coverage", ""])
    scored = [c for c in report.coverage if c.get("coverage_score") is not None]
    if not scored:
        lines.append("_No coverage scores._")
        lines.append("")
    else:
        mean = sum(float(c["coverage_score"]) for c in scored) / len(scored)
        lines.append(f"Mean coverage score: **{mean:.3f}** ({len(scored)} events)")
        lines.append("")
        lines.append("| Event | Score | Status |")
        lines.append("|---|---:|---|")
        for c in report.coverage:
            score = c.get("coverage_score")
            score_s = f"{score:.3f}" if score is not None else "—"
            lines.append(f"| {c.get('key')} | {score_s} | {c.get('status')} |")
        lines.append("")

    lines.extend(["## Variance", ""])
    if not report.variance:
        lines.append("_No events with multiple non-superseded runs._")
        lines.append("")
    else:
        lines.append(
            "Events with more than one non-superseded / non-abandoned run "
            "(latest is used for scoring):"
        )
        lines.append("")
        for row in report.variance:
            keys = ", ".join(row.get("expectation_keys") or []) or "—"
            lines.append(
                f"**{row.get('replay_key') or row.get('event_id')}** (expectations: {keys})"
            )
            for run in row.get("runs") or []:
                lines.append(
                    f"- `{run.get('run_id')}` — {run.get('got') or run.get('status')} "
                    f"({run.get('created_at') or '—'})"
                )
            lines.append("")

    t = report.totals
    lines.extend(
        [
            "## Cost, tokens, turns",
            "",
            f"- Expectations: {t.get('events', 0)}",
            f"- Total cost (USD): {t.get('cost_usd', 0):.6f}",
            f"- Total tokens: {t.get('tokens', 0)}",
            f"- Total turns: {t.get('turns', 0)}",
            "",
            "| Event | Date | Expected | Got | Status | Turns | Tokens | Cost |",
            "|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    for r in report.per_event:
        exp_s = "—" if r.expected_alert is None else ("alert" if r.expected_alert else "log")
        lines.append(
            f"| {r.replay_key or '—'} | {r.event_date} | {exp_s} | {r.got or '—'} | "
            f"{r.status} | {r.turns} | {r.tokens} | {r.cost_usd:.6f} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_eval_reports(
    report: EvalReport,
    base_path: Path,
) -> tuple[Path, Path]:
    """Write ``<base>_eval.json`` and ``<base>_eval.md`` next to the replay log."""
    # base_path is the .json log path → stem_eval.md / stem_eval.json
    stem = base_path.with_suffix("")
    json_path = Path(f"{stem}_eval.json")
    md_path = Path(f"{stem}_eval.md")
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_eval_markdown(report), encoding="utf-8")
    return md_path, json_path


def evaluate_latest(
    session: Session,
    *,
    replay_path: Path | str | None = None,
) -> EvalReport:
    """Score all expectations against the latest non-superseded run per event."""
    return evaluate_replay(
        session,
        replay_path=replay_path,
        log=None,
        prefer_latest_run=True,
    )


def write_latest_eval_reports(
    report: EvalReport,
    runs_dir: Path,
) -> tuple[Path, Path]:
    """Write ``replay_runs/latest_eval.md`` and ``.json`` for the dashboard."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    json_path = runs_dir / "latest_eval.json"
    md_path = runs_dir / "latest_eval.md"
    payload = report.to_dict()
    payload["evaluated_at"] = datetime.now(UTC).isoformat()
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_eval_markdown(report), encoding="utf-8")
    return md_path, json_path
