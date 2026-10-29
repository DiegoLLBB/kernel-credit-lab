"""event_filing link table; replay_expectation confirming_sources + notes

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_filing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("filing_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["filing_id"], ["filing.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "filing_id", name="uq_event_filing"),
    )
    op.create_index(op.f("ix_event_filing_event_id"), "event_filing", ["event_id"])
    op.create_index(op.f("ix_event_filing_filing_id"), "event_filing", ["filing_id"])

    op.add_column(
        "replay_expectation",
        sa.Column("confirming_sources", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("replay_expectation", sa.Column("notes", sa.Text(), nullable=True))
    op.alter_column("replay_expectation", "confirming_sources", server_default=None)


def downgrade() -> None:
    op.drop_column("replay_expectation", "notes")
    op.drop_column("replay_expectation", "confirming_sources")
    op.drop_index(op.f("ix_event_filing_filing_id"), table_name="event_filing")
    op.drop_index(op.f("ix_event_filing_event_id"), table_name="event_filing")
    op.drop_table("event_filing")
