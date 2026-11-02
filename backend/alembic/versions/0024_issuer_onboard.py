"""Issuer onboard columns: xbrl_config, onboarded_at, watchlist.monitor_from.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("issuer", sa.Column("xbrl_config", _json(), nullable=True))
    op.add_column(
        "issuer",
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "issuer",
        sa.Column("onboard_source", sa.String(length=16), nullable=True),
    )
    op.add_column("watchlist", sa.Column("monitor_from", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("watchlist", "monitor_from")
    op.drop_column("issuer", "onboard_source")
    op.drop_column("issuer", "onboarded_at")
    op.drop_column("issuer", "xbrl_config")
