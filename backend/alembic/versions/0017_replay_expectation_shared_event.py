"""Allow multiple ReplayExpectations to share one Event (combined filings).

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_replay_expectation_event_id", table_name="replay_expectation")
    op.create_index(
        "ix_replay_expectation_event_id",
        "replay_expectation",
        ["event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_replay_expectation_event_id", table_name="replay_expectation")
    op.create_index(
        "ix_replay_expectation_event_id",
        "replay_expectation",
        ["event_id"],
        unique=True,
    )
