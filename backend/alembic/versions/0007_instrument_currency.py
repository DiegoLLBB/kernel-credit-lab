"""add instrument.currency (ISO 4217)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28 14:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instrument",
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
    )
    op.alter_column("instrument", "currency", server_default=None)


def downgrade() -> None:
    op.drop_column("instrument", "currency")
