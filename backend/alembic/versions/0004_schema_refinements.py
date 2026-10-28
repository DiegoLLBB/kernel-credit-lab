"""schema refinements: alert findings, checklist key, event status, audit trigger

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 11:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("issuer", sa.Column("ticker", sa.String(length=16), nullable=True))
    op.add_column("issuer", sa.Column("sector", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_issuer_ticker"), "issuer", ["ticker"], unique=False)

    op.add_column("instrument", sa.Column("reference_rate", sa.String(length=32), nullable=True))
    op.add_column("instrument", sa.Column("spread_bps", sa.Integer(), nullable=True))

    op.add_column(
        "event",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column("event", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        op.f("fk_event_run_id_research_run"),
        "event",
        "research_run",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_event_run_id"), "event", ["run_id"], unique=False)
    op.create_index(op.f("ix_event_status"), "event", ["status"], unique=False)
    op.alter_column("event", "status", server_default=None)

    op.add_column("alert", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("alert", sa.Column("findings", JsonType, nullable=True))

    op.add_column(
        "checklist_item",
        sa.Column("key", sa.String(length=64), nullable=False, server_default="item"),
    )
    op.create_unique_constraint("uq_checklist_item_run_key", "checklist_item", ["run_id", "key"])
    op.alter_column("checklist_item", "key", server_default=None)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_entry_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_entry is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entry_append_only
        BEFORE UPDATE OR DELETE ON audit_entry
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_entry_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_entry_append_only ON audit_entry")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_entry_mutation()")

    op.drop_constraint("uq_checklist_item_run_key", "checklist_item", type_="unique")
    op.drop_column("checklist_item", "key")

    op.drop_column("alert", "findings")
    op.drop_column("alert", "summary")

    op.drop_index(op.f("ix_event_status"), table_name="event")
    op.drop_index(op.f("ix_event_run_id"), table_name="event")
    op.drop_constraint(op.f("fk_event_run_id_research_run"), "event", type_="foreignkey")
    op.drop_column("event", "run_id")
    op.drop_column("event", "status")

    op.drop_column("instrument", "spread_bps")
    op.drop_column("instrument", "reference_rate")

    op.drop_index(op.f("ix_issuer_ticker"), table_name="issuer")
    op.drop_column("issuer", "sector")
    op.drop_column("issuer", "ticker")
