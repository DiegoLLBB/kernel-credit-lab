"""instrument.maturity_precision for month-only maturities

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instrument",
        sa.Column(
            "maturity_precision",
            sa.String(length=8),
            nullable=False,
            server_default="day",
        ),
    )
    op.alter_column("instrument", "maturity_precision", server_default=None)


def downgrade() -> None:
    op.drop_column("instrument", "maturity_precision")
