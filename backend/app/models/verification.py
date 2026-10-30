"""Per-run verification records: checklist, facts, in-tool checks and append-only audit.

The model reasons; checks that do not trust the model decide whether work may move forward.
See docs/CONTEXT.md §9.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.trace import TimestampMixin

if TYPE_CHECKING:
    from app.models.credit import Filing

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class ChecklistItem(TimestampMixin, Base):
    """What an event run must settle: key, question, evidence needed, status."""

    __tablename__ = "checklist_item"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_checklist_item_run_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True, nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_needed: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", index=True
    )  # open | proven | escalated

    facts: Mapped[list["Fact"]] = relationship(
        back_populates="checklist_item", cascade="all, delete-orphan"
    )


class Fact(TimestampMixin, Base):
    """Recorded evidence: document/filing, location, exact quote, and claim (interpretation).

    ``fact_type`` is ``quote`` (default) or ``chart_reading``. Chart readings store
    ``meta = {chart_id, category, value}`` and are accepted as evidence only when the
    linked ChartReading is verified.
    """

    __tablename__ = "fact"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True, nullable=False
    )
    checklist_item_id: Mapped[str] = mapped_column(
        ForeignKey("checklist_item.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filing_id: Mapped[str | None] = mapped_column(
        ForeignKey("filing.id", ondelete="SET NULL"), index=True
    )
    location: Mapped[str | None] = mapped_column(String(256))
    exact_quote: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(String(32), nullable=False, default="quote", index=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JsonType)

    checklist_item: Mapped[ChecklistItem] = relationship(back_populates="facts")
    filing: Mapped["Filing | None"] = relationship()


class CheckResult(TimestampMixin, Base):
    """Outcome of an in-tool check (coverage, quote, evidence, alert rules, claims)."""

    __tablename__ = "check_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True, nullable=False
    )
    check_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    problems: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JsonType)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AuditEntry(Base):
    """Append-only log of actions by agent, verification and analyst. Never update rows."""

    __tablename__ = "audit_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # agent | verification | analyst
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
