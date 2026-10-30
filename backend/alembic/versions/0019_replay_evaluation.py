"""ReplayEvaluation table: evaluation results stored in the database.

Cloud Run's filesystem is discarded after each execution, so replay eval JSON files
never reach the API. run_replay --eval-latest writes one row per evaluation; the
dashboard reads the latest row (falling back to the files only when empty).

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("precision", sa.Numeric(6, 5), nullable=True),
        sa.Column("recall", sa.Numeric(6, 5), nullable=True),
        sa.Column("incomplete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=True),
        sa.Column("expectation_count", sa.Integer(), nullable=True),
        sa.Column("per_event", _json(), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_replay_evaluation_evaluated_at", "replay_evaluation", ["evaluated_at"])


def downgrade() -> None:
    op.drop_index("ix_replay_evaluation_evaluated_at", table_name="replay_evaluation")
    op.drop_table("replay_evaluation")
