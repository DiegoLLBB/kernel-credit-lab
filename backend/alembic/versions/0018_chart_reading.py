"""ChartReading table; Filing.content_bytes; Fact.fact_type/meta.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("filing", sa.Column("content_bytes", sa.LargeBinary(), nullable=True))
    op.add_column(
        "fact",
        sa.Column("fact_type", sa.String(length=32), nullable=False, server_default="quote"),
    )
    op.add_column("fact", sa.Column("meta", _json(), nullable=True))
    op.create_index("ix_fact_fact_type", "fact", ["fact_type"])

    op.create_table(
        "chart_reading",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "issuer_id",
            sa.String(length=36),
            sa.ForeignKey("issuer.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "filing_id",
            sa.String(length=36),
            sa.ForeignKey("filing.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("bbox", _json(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("unit", sa.String(length=128), nullable=True),
        sa.Column("categories", _json(), nullable=False),
        sa.Column("values", _json(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_detail", _json(), nullable=True),
        sa.Column("crop_png", sa.LargeBinary(), nullable=True),
        sa.Column("overlay_png", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_chart_reading_issuer_id", "chart_reading", ["issuer_id"])
    op.create_index("ix_chart_reading_filing_id", "chart_reading", ["filing_id"])
    op.create_index("ix_chart_reading_page", "chart_reading", ["page"])
    op.create_index("ix_chart_reading_verified", "chart_reading", ["verified"])


def downgrade() -> None:
    op.drop_index("ix_chart_reading_verified", table_name="chart_reading")
    op.drop_index("ix_chart_reading_page", table_name="chart_reading")
    op.drop_index("ix_chart_reading_filing_id", table_name="chart_reading")
    op.drop_index("ix_chart_reading_issuer_id", table_name="chart_reading")
    op.drop_table("chart_reading")
    op.drop_index("ix_fact_fact_type", table_name="fact")
    op.drop_column("fact", "meta")
    op.drop_column("fact", "fact_type")
    op.drop_column("filing", "content_bytes")
