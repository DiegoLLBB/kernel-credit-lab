"""credit_metric table: XBRL-derived credit metrics with point-in-time visibility.

A row becomes visible on its ``filed`` date; NULL value + detail.missing reason
marks metrics that cannot be built from tagged data (never estimated).

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "credit_metric",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_label", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Numeric(24, 4), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=False, server_default="USD"),
        sa.Column("source_tag", sa.String(length=512), nullable=True),
        sa.Column("accession", sa.String(length=32), nullable=True),
        sa.Column("filed", sa.Date(), nullable=True),
        sa.Column("detail", _json(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "issuer_id", "metric", "period_end", name="uq_credit_metric_issuer_metric_end"
        ),
    )
    op.create_index("ix_credit_metric_issuer_id", "credit_metric", ["issuer_id"])
    op.create_index("ix_credit_metric_metric", "credit_metric", ["metric"])
    op.create_index("ix_credit_metric_period_end", "credit_metric", ["period_end"])
    op.create_index("ix_credit_metric_filed", "credit_metric", ["filed"])


def downgrade() -> None:
    op.drop_index("ix_credit_metric_filed", table_name="credit_metric")
    op.drop_index("ix_credit_metric_period_end", table_name="credit_metric")
    op.drop_index("ix_credit_metric_metric", table_name="credit_metric")
    op.drop_index("ix_credit_metric_issuer_id", table_name="credit_metric")
    op.drop_table("credit_metric")
