"""credit_insight table: checked insight text with inputs and verdicts.

One row per (issuer, as_of, scope, ref). Sentences hold kept text only;
dropped sentences and refusal reasons live in the dropped/check columns.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "credit_insight",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("sentences", _json(), nullable=False),
        sa.Column("dropped", _json(), nullable=False),
        sa.Column("facts", _json(), nullable=True),
        sa.Column("check", _json(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="empty"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "issuer_id", "as_of", "scope", "ref", name="uq_credit_insight_issuer_scope"
        ),
    )
    op.create_index("ix_credit_insight_issuer_id", "credit_insight", ["issuer_id"])
    op.create_index("ix_credit_insight_as_of", "credit_insight", ["as_of"])
    op.create_index("ix_credit_insight_scope", "credit_insight", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_credit_insight_scope", table_name="credit_insight")
    op.drop_index("ix_credit_insight_as_of", table_name="credit_insight")
    op.drop_index("ix_credit_insight_issuer_id", table_name="credit_insight")
    op.drop_table("credit_insight")
