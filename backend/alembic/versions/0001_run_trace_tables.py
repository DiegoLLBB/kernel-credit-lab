"""run trace tables

Revision ID: 0001
Revises:
Create Date: 2026-10-27 13:11:43.556631
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector powers semantic retrieval over document chunks (tables added in 0002).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "research_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("workflow", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column(
            "result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_run_status"), "research_run", ["status"], unique=False)
    op.create_table(
        "agent_step",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column(
            "output",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_step_run_id"), "agent_step", ["run_id"], unique=False)
    op.create_table(
        "model_call",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("step_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("task", sa.String(length=96), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_key", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_step.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_call_cache_key"), "model_call", ["cache_key"], unique=False)
    op.create_index(op.f("ix_model_call_model"), "model_call", ["model"], unique=False)
    op.create_index(op.f("ix_model_call_run_id"), "model_call", ["run_id"], unique=False)
    op.create_table(
        "tool_call",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("step_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=96), nullable=False),
        sa.Column(
            "arguments",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_step.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_call_run_id"), "tool_call", ["run_id"], unique=False)
    op.create_index(op.f("ix_tool_call_tool_name"), "tool_call", ["tool_name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_call_tool_name"), table_name="tool_call")
    op.drop_index(op.f("ix_tool_call_run_id"), table_name="tool_call")
    op.drop_table("tool_call")
    op.drop_index(op.f("ix_model_call_run_id"), table_name="model_call")
    op.drop_index(op.f("ix_model_call_model"), table_name="model_call")
    op.drop_index(op.f("ix_model_call_cache_key"), table_name="model_call")
    op.drop_table("model_call")
    op.drop_index(op.f("ix_agent_step_run_id"), table_name="agent_step")
    op.drop_table("agent_step")
    op.drop_index(op.f("ix_research_run_status"), table_name="research_run")
    op.drop_table("research_run")
