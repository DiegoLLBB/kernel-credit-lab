"""issuer.key, event.replay_key, and replay_expectation for eval

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29 10:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("issuer", sa.Column("key", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_issuer_key"), "issuer", ["key"], unique=True)

    op.add_column("event", sa.Column("replay_key", sa.String(length=128), nullable=True))
    op.create_index(op.f("ix_event_replay_key"), "event", ["replay_key"], unique=True)

    op.create_table(
        "replay_expectation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("issuer_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("available_from", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("expected_changes", sa.JSON(), nullable=False),
        sa.Column("expected_alert", sa.Boolean(), nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        op.f("ix_replay_expectation_available_from"),
        "replay_expectation",
        ["available_from"],
        unique=False,
    )
    op.create_index(op.f("ix_replay_expectation_key"), "replay_expectation", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_replay_expectation_key"), table_name="replay_expectation")
    op.drop_index(op.f("ix_replay_expectation_available_from"), table_name="replay_expectation")
    op.drop_table("replay_expectation")
    op.drop_index(op.f("ix_event_replay_key"), table_name="event")
    op.drop_column("event", "replay_key")
    op.drop_index(op.f("ix_issuer_key"), table_name="issuer")
    op.drop_column("issuer", "key")
