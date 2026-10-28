"""add state_change.effective_date for replay

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28 14:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("state_change", sa.Column("effective_date", sa.Date(), nullable=True))
    op.create_index(
        op.f("ix_state_change_effective_date"), "state_change", ["effective_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_state_change_effective_date"), table_name="state_change")
    op.drop_column("state_change", "effective_date")
