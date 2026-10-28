"""add instrument.key for seed idempotency

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-28 12:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("instrument", sa.Column("key", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_instrument_key"), "instrument", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_instrument_key"), table_name="instrument")
    op.drop_column("instrument", "key")
