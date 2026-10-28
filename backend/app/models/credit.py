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
    Date,
    DateTime,
    ForeignKey,
    Integer,
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
    accession_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    filed_at: Mapped[date | None] = mapped_column(Date, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(512))

    issuer: Mapped[Issuer | None] = relationship()
    events: Mapped[list["Event"]] = relationship(back_populates="source_filing")


class Event(TimestampMixin, Base):
    """Typed corporate or macro event that can trigger an agent run."""

    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
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
    alerts: Mapped[list["Alert"]] = relationship(back_populates="event")


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
