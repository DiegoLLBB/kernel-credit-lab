"""Alert.kind for metric vs agent alerts.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="agent"),
    )
    op.create_index("ix_alert_kind", "alert", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_alert_kind", table_name="alert")
    op.drop_column("alert", "kind")
