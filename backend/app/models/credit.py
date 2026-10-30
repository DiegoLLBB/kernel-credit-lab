"""Credit-domain data model: issuers, instruments, filings, events, alerts and provenance.

Postgres is the source of truth for structured capital-structure state. Vector search is a
retrieval aid only; every curated seed field should have a CuratedSource record.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.trace import TimestampMixin

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class Issuer(TimestampMixin, Base):
    """Company or obligor; parent_id links opcos to a holdco."""

    __tablename__ = "issuer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("issuer.id", ondelete="SET NULL"), index=True
    )

    parent: Mapped["Issuer | None"] = relationship(
        remote_side="Issuer.id", back_populates="subsidiaries"
    )
    subsidiaries: Mapped[list["Issuer"]] = relationship(back_populates="parent")
    instruments: Mapped[list["Instrument"]] = relationship(back_populates="issuer")


class Instrument(TimestampMixin, Base):
    """Bond or loan. Loans use instrument_type='loan' (same table, not a separate model)."""

    __tablename__ = "instrument"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("issuer.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    instrument_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="bond", index=True
    )  # bond | loan
    coupon: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    rate_type: Mapped[str] = mapped_column(String(16), nullable=False, default="fixed")
    # fixed | floating
    reference_rate: Mapped[str | None] = mapped_column(String(32))  # e.g. SOFR
    spread_bps: Mapped[int | None] = mapped_column(Integer)
    maturity: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # day | month — month when the source gives only month+year (stored as 1st of month).
    maturity_precision: Mapped[str] = mapped_column(String(8), nullable=False, default="day")
    issue_date: Mapped[date | None] = mapped_column(Date)
    amount_outstanding: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    seniority: Mapped[str | None] = mapped_column(String(64))
    security: Mapped[str | None] = mapped_column(String(64))
    cusip: Mapped[str | None] = mapped_column(String(12), index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True
    )  # active | redeemed | matured

    issuer: Mapped[Issuer] = relationship(back_populates="instruments")
    call_terms: Mapped["CallTerms | None"] = relationship(
        back_populates="instrument", uselist=False, cascade="all, delete-orphan"
    )
    curated_sources: Mapped[list["CuratedSource"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class CallTerms(TimestampMixin, Base):
    """IG call features: make-whole plus a par-call window near maturity."""

    __tablename__ = "call_terms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    par_call_date: Mapped[date | None] = mapped_column(Date)
    make_whole_spread_bps: Mapped[int | None] = mapped_column(Integer)

    instrument: Mapped[Instrument] = relationship(back_populates="call_terms")


class CuratedSource(TimestampMixin, Base):
    """Provenance for a hand-entered seed field on an instrument."""

    __tablename__ = "curated_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), index=True, nullable=False
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    instrument: Mapped[Instrument] = relationship(back_populates="curated_sources")


class Filing(TimestampMixin, Base):
    """SEC or issuer document; content_hash supports idempotent ingestion."""

    __tablename__ = "filing"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuer_id: Mapped[str | None] = mapped_column(
        ForeignKey("issuer.id", ondelete="SET NULL"), index=True
    )
    form_type: Mapped[str | None] = mapped_column(String(32), index=True)
    # Nullable so issuer press releases (no SEC accession) can be stored; unique when set.
    accession_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    # Idempotency key for press releases and a stable identity for any filing URL.
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    filed_at: Mapped[date | None] = mapped_column(Date, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    # Extracted plain text for agent read_document (HTML/PDF extracted once at ingest).
    body_text: Mapped[str | None] = mapped_column(Text)
    # Raw PDF/HTML bytes for presentations (SAM page render); optional for other forms.
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)

    issuer: Mapped[Issuer | None] = relationship()
    events: Mapped[list["Event"]] = relationship(back_populates="source_filing")
    chart_readings: Mapped[list["ChartReading"]] = relationship(back_populates="filing")


class Event(TimestampMixin, Base):
    """Typed corporate or macro event that can trigger an agent run.

    ``replay_key`` idempotently identifies seeded/calendar/rate events. Evaluation
    fields (expected_changes, expected_alert, why) live only on ``ReplayExpectation``.
    """

    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    replay_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    issuer_id: Mapped[str | None] = mapped_column(
        ForeignKey("issuer.id", ondelete="SET NULL"), index=True
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_filing_id: Mapped[str | None] = mapped_column(
        ForeignKey("filing.id", ondelete="SET NULL"), index=True
    )
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )  # pending | processing | processed | failed
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="SET NULL"), index=True
    )

    issuer: Mapped[Issuer | None] = relationship()
    source_filing: Mapped[Filing | None] = relationship(back_populates="events")
    event_filings: Mapped[list["EventFiling"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(back_populates="event")
    replay_expectations: Mapped[list["ReplayExpectation"]] = relationship(back_populates="event")


class EventFiling(TimestampMixin, Base):
    """Many-to-many link between an Event and its eligible source Filings."""

    __tablename__ = "event_filing"
    __table_args__ = (UniqueConstraint("event_id", "filing_id", name="uq_event_filing"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("filing.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event: Mapped[Event] = relationship(back_populates="event_filings")
    filing: Mapped[Filing] = relationship()


class ReplayExpectation(TimestampMixin, Base):
    """Evaluation reference for a seeded replay event — never shown to the agent."""

    __tablename__ = "replay_expectation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    issuer_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    confirming_sources: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    expected_changes: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    expected_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    why: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    # Multiple expectations may share one Event when they share a source filing
    # (e.g. combined Duke 10-Q covering several registrants).
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("event.id", ondelete="SET NULL"), index=True
    )

    event: Mapped[Event | None] = relationship(back_populates="replay_expectations")


class StateChange(TimestampMixin, Base):
    """Proposed/applied/rejected diff to structured state, with evidence and verifier result.

    Applied instrument diffs use proposed_diff =
    ``{"field": "amount_outstanding"|"status", "before": ..., "after": ...}`` and
    ``effective_date`` so maturity_wall can reconstruct state at any past date.
    """

    __tablename__ = "state_change"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instrument.id", ondelete="SET NULL"), index=True
    )
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("event.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="SET NULL"), index=True
    )
    proposed_diff: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    verifier_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", index=True
    )  # proposed | applied | rejected
    effective_date: Mapped[date | None] = mapped_column(Date, index=True)

    instrument: Mapped[Instrument | None] = relationship()
    event: Mapped[Event | None] = relationship()


class Alert(TimestampMixin, Base):
    """Material finding for the alerts feed; analysts confirm or dismiss."""

    __tablename__ = "alert"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("event.id", ondelete="SET NULL"), index=True
    )
    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("issuer.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[list[Any] | None] = mapped_column(JsonType)
    # list of {item, statement, citations}
    before_metrics: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    after_metrics: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    materiality_reasons: Mapped[list[Any] | None] = mapped_column(JsonType)
    # Independent claims check result: {status, problems, findings}.
    claims_check: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", index=True
    )  # new | confirmed | dismissed

    event: Mapped[Event | None] = relationship(back_populates="alerts")
    issuer: Mapped[Issuer] = relationship()


class Watchlist(TimestampMixin, Base):
    """Issuers the analyst monitors. One row per watched issuer."""

    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("issuer_id", name="uq_watchlist_issuer_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("issuer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(String(128))

    issuer: Mapped[Issuer] = relationship()


class MetricSnapshot(TimestampMixin, Base):
    """Per-issuer computed metrics at a point in time (before/after materiality)."""

    __tablename__ = "metric_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("issuer.id", ondelete="CASCADE"), index=True, nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="SET NULL"), index=True
    )

    issuer: Mapped[Issuer] = relationship()


class MaturitySchedule(TimestampMixin, Base):
    """Issuer debt maturity wall from a filing (XBRL / 10-K schedule).

    buckets: list of {label, period_end, amount}. Unique per issuer and as_of.
    """

    __tablename__ = "maturity_schedule"
    __table_args__ = (
        UniqueConstraint("issuer_id", "as_of", name="uq_maturity_schedule_issuer_as_of"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("issuer.id", ondelete="CASCADE"), index=True, nullable=False
    )
    basis: Mapped[str] = mapped_column(String(32), nullable=False)  # consolidated | registrant
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    buckets: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)

    issuer: Mapped[Issuer] = relationship()


class RateObservation(TimestampMixin, Base):
    """Daily FRED observation (Treasury yield or FX) for refinancing / replay."""

    __tablename__ = "rate_observation"
    __table_args__ = (
        UniqueConstraint("series_id", "obs_date", name="uq_rate_observation_series_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    series_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    obs_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)


class ChartReading(TimestampMixin, Base):
    """SAM + Spark chart extraction with geometry verification (CONTEXT.md Part B §5)."""

    __tablename__ = "chart_reading"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuer_id: Mapped[str | None] = mapped_column(
        ForeignKey("issuer.id", ondelete="SET NULL"), index=True
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("filing.id", ondelete="CASCADE"), index=True, nullable=False
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # SAM frame coordinates for the chart region.
    bbox: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    title: Mapped[str | None] = mapped_column(String(512))
    unit: Mapped[str | None] = mapped_column(String(128))
    categories: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    values: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # status/reason/checks/ratios; kept for unverified rows too.
    verification_detail: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    crop_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    overlay_png: Mapped[bytes | None] = mapped_column(LargeBinary)

    issuer: Mapped[Issuer | None] = relationship()
    filing: Mapped[Filing] = relationship(back_populates="chart_readings")


class ReplayEvaluation(TimestampMixin, Base):
    """Persisted replay evaluation summary (one row per ``--eval-latest`` run).

    Cloud Run jobs have no durable filesystem, so the dashboard reads the latest
    row here instead of ``replay_runs/latest_eval.json``. The JSON files are still
    written locally for inspection; per-event rows live in ``per_event``.
    """

    __tablename__ = "replay_evaluation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    precision: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    recall: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    incomplete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    event_count: Mapped[int | None] = mapped_column(Integer)
    expectation_count: Mapped[int | None] = mapped_column(Integer)
    per_event: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
