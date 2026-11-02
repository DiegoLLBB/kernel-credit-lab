"""Daily brief table + replay_evaluation kind / unsupported_claims.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "daily_brief",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("sentences", _json(), nullable=False),
        sa.Column("dropped", _json(), nullable=False),
        sa.Column("facts", _json(), nullable=True),
        sa.Column("check", _json(), nullable=True),
        sa.Column("links", _json(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="empty"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("as_of", name="uq_daily_brief_as_of"),
    )
    op.create_index("ix_daily_brief_as_of", "daily_brief", ["as_of"])

    op.add_column(
        "replay_evaluation",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="agent"),
    )
    op.add_column(
        "replay_evaluation",
        sa.Column("unsupported_claims", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_replay_evaluation_kind", "replay_evaluation", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_replay_evaluation_kind", table_name="replay_evaluation")
    op.drop_column("replay_evaluation", "unsupported_claims")
    op.drop_column("replay_evaluation", "kind")
    op.drop_index("ix_daily_brief_as_of", table_name="daily_brief")
    op.drop_table("daily_brief")
