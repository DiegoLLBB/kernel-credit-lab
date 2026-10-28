"""verification tables (checklist, fact, check result, audit)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 11:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "checklist_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("evidence_needed", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checklist_item_run_id"), "checklist_item", ["run_id"], unique=False)
    op.create_index(op.f("ix_checklist_item_status"), "checklist_item", ["status"], unique=False)

    op.create_table(
        "fact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("checklist_item_id", sa.String(length=36), nullable=False),
        sa.Column("filing_id", sa.String(length=36), nullable=True),
        sa.Column("location", sa.String(length=256), nullable=True),
        sa.Column("exact_quote", sa.Text(), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["checklist_item_id"], ["checklist_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["filing_id"], ["filing.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_fact_checklist_item_id"), "fact", ["checklist_item_id"], unique=False)
    op.create_index(op.f("ix_fact_filing_id"), "fact", ["filing_id"], unique=False)
    op.create_index(op.f("ix_fact_run_id"), "fact", ["run_id"], unique=False)

    op.create_table(
        "check_result",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("check_name", sa.String(length=64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("problems", JsonType, nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_check_result_check_name"), "check_result", ["check_name"], unique=False
    )
    op.create_index(op.f("ix_check_result_run_id"), "check_result", ["run_id"], unique=False)

    op.create_table(
        "audit_entry",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("detail", JsonType, nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_entry_action"), "audit_entry", ["action"], unique=False)
    op.create_index(op.f("ix_audit_entry_actor"), "audit_entry", ["actor"], unique=False)
    op.create_index(op.f("ix_audit_entry_run_id"), "audit_entry", ["run_id"], unique=False)
    op.create_index(op.f("ix_audit_entry_timestamp"), "audit_entry", ["timestamp"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_entry_timestamp"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_run_id"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_actor"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_action"), table_name="audit_entry")
    op.drop_table("audit_entry")

    op.drop_index(op.f("ix_check_result_run_id"), table_name="check_result")
    op.drop_index(op.f("ix_check_result_check_name"), table_name="check_result")
    op.drop_table("check_result")

    op.drop_index(op.f("ix_fact_run_id"), table_name="fact")
    op.drop_index(op.f("ix_fact_filing_id"), table_name="fact")
    op.drop_index(op.f("ix_fact_checklist_item_id"), table_name="fact")
    op.drop_table("fact")

    op.drop_index(op.f("ix_checklist_item_status"), table_name="checklist_item")
    op.drop_index(op.f("ix_checklist_item_run_id"), table_name="checklist_item")
    op.drop_table("checklist_item")
