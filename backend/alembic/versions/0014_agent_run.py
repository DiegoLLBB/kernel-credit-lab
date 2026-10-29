"""agent run fields on research_run; filing.body_text

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("filing", sa.Column("body_text", sa.Text(), nullable=True))

    op.add_column("research_run", sa.Column("event_id", sa.String(length=36), nullable=True))
    op.add_column("research_run", sa.Column("event_kind", sa.String(length=64), nullable=True))
    op.add_column(
        "research_run",
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_run",
        sa.Column("refusal_counts", JsonType, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "research_run",
        sa.Column("pages_read", JsonType, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("research_run", sa.Column("coverage_score", JsonType, nullable=True))
    op.add_column(
        "research_run",
        sa.Column("metrics_outputs", JsonType, nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_foreign_key(
        op.f("fk_research_run_event_id_event"),
        "research_run",
        "event",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_research_run_event_id"), "research_run", ["event_id"])
    op.alter_column("research_run", "turn_count", server_default=None)
    op.alter_column("research_run", "refusal_counts", server_default=None)
    op.alter_column("research_run", "pages_read", server_default=None)
    op.alter_column("research_run", "metrics_outputs", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_research_run_event_id"), table_name="research_run")
    op.drop_constraint(op.f("fk_research_run_event_id_event"), "research_run", type_="foreignkey")
    op.drop_column("research_run", "metrics_outputs")
    op.drop_column("research_run", "coverage_score")
    op.drop_column("research_run", "pages_read")
    op.drop_column("research_run", "refusal_counts")
    op.drop_column("research_run", "turn_count")
    op.drop_column("research_run", "event_kind")
    op.drop_column("research_run", "event_id")
    op.drop_column("filing", "body_text")
