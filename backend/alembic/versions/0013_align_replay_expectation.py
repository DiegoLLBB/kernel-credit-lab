"""align replay_expectation with its model: jsonb columns, unique indexes

Migration 0011 created three JSON columns as `json` (the model uses `jsonb` on Postgres, like
every other table) and enforced uniqueness with constraints plus a non-unique index, where the
model declares unique indexes. Data and behaviour are unchanged; this removes the drift that
`alembic check` reports in CI.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_COLUMNS = ("sources", "confirming_sources", "expected_changes")


def upgrade() -> None:
    for column in _JSON_COLUMNS:
        op.alter_column(
            "replay_expectation",
            column,
            type_=postgresql.JSONB(),
            existing_type=postgresql.JSON(),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )
    op.drop_constraint("replay_expectation_key_key", "replay_expectation", type_="unique")
    op.drop_constraint("replay_expectation_event_id_key", "replay_expectation", type_="unique")
    op.drop_index("ix_replay_expectation_key", table_name="replay_expectation")
    op.create_index("ix_replay_expectation_key", "replay_expectation", ["key"], unique=True)
    op.create_index(
        "ix_replay_expectation_event_id", "replay_expectation", ["event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_replay_expectation_event_id", table_name="replay_expectation")
    op.drop_index("ix_replay_expectation_key", table_name="replay_expectation")
    op.create_index("ix_replay_expectation_key", "replay_expectation", ["key"], unique=False)
    op.create_unique_constraint(
        "replay_expectation_event_id_key", "replay_expectation", ["event_id"]
    )
    op.create_unique_constraint("replay_expectation_key_key", "replay_expectation", ["key"])
    for column in _JSON_COLUMNS:
        op.alter_column(
            "replay_expectation",
            column,
            type_=postgresql.JSON(),
            existing_type=postgresql.JSONB(),
            existing_nullable=False,
            postgresql_using=f"{column}::json",
        )
