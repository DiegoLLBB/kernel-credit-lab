"""maturity schedule table

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28 12:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "maturity_schedule",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("buckets", JsonType, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer_id", "as_of", name="uq_maturity_schedule_issuer_as_of"),
    )
    op.create_index(
        op.f("ix_maturity_schedule_as_of"), "maturity_schedule", ["as_of"], unique=False
    )
    op.create_index(
        op.f("ix_maturity_schedule_issuer_id"), "maturity_schedule", ["issuer_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_maturity_schedule_issuer_id"), table_name="maturity_schedule")
    op.drop_index(op.f("ix_maturity_schedule_as_of"), table_name="maturity_schedule")
    op.drop_table("maturity_schedule")
