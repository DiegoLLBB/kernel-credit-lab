"""live_config freeze row + event.source for live vs replay.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "live_config",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("go_live_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "event",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="replay"),
    )
    op.create_index("ix_event_source", "event", ["source"])
    op.add_column("replay_evaluation", sa.Column("alert_counts", _json(), nullable=True))


def downgrade() -> None:
    op.drop_column("replay_evaluation", "alert_counts")
    op.drop_index("ix_event_source", table_name="event")
    op.drop_column("event", "source")
    op.drop_table("live_config")
