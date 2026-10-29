"""Run-trace data model: every research run, agent step, tool call and model call is persisted.

This is the observability backbone of Kernel: the UI's run trace, cost/latency reporting and
the evaluation report are all built from these tables.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResearchRun(TimestampMixin, Base):
    """Agent / research run.

    Event-driven runs use status running|alerted|logged|needs_input|paused.
    """

    __tablename__ = "research_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    workflow: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), default="standard")  # standard | deep
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    state: Mapped[str] = mapped_column(String(48), default="INTAKE")
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per-event agent loop (CONTEXT.md §9); null for free-form research questions.
    # use_alter: Event.run_id already FKs research_run (cycle).
    event_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "event.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_research_run_event_id_event",
        ),
        index=True,
    )
    event_kind: Mapped[str | None] = mapped_column(String(64))
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refusal_counts: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    pages_read: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    # Replay-only coverage score vs ReplayExpectation; never shown in agent feedback.
    coverage_score: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    metrics_outputs: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", order_by="AgentStep.sequence", cascade="all, delete-orphan"
    )


# Alias used by the agent tool layer.
AgentRun = ResearchRun


class AgentStep(TimestampMixin, Base):
    __tablename__ = "agent_step"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[ResearchRun] = relationship(back_populates="steps")


class ToolCall(TimestampMixin, Base):
    __tablename__ = "tool_call"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str | None] = mapped_column(ForeignKey("agent_step.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ModelCall(TimestampMixin, Base):
    __tablename__ = "model_call"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_run.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str | None] = mapped_column(ForeignKey("agent_step.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # meta | baseten
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task: Mapped[str | None] = mapped_column(String(96))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success | invalid | error
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cache_key: Mapped[str | None] = mapped_column(String(128), index=True)
    error: Mapped[str | None] = mapped_column(Text)
