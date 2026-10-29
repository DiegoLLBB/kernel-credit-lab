"""add rate_observation for FRED yields and FX

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28 18:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_observation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.String(length=32), nullable=False),
        sa.Column("obs_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "obs_date", name="uq_rate_observation_series_date"),
    )
    op.create_index(op.f("ix_rate_observation_obs_date"), "rate_observation", ["obs_date"])
    op.create_index(op.f("ix_rate_observation_series_id"), "rate_observation", ["series_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_rate_observation_series_id"), table_name="rate_observation")
    op.drop_index(op.f("ix_rate_observation_obs_date"), table_name="rate_observation")
    op.drop_table("rate_observation")
