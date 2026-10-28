"""credit domain tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 11:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "issuer",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["issuer.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_issuer_cik"), "issuer", ["cik"], unique=False)
    op.create_index(op.f("ix_issuer_parent_id"), "issuer", ["parent_id"], unique=False)

    op.create_table(
        "instrument",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("instrument_type", sa.String(length=16), nullable=False),
        sa.Column("coupon", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("rate_type", sa.String(length=16), nullable=False),
        sa.Column("maturity", sa.Date(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("amount_outstanding", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("seniority", sa.String(length=64), nullable=True),
        sa.Column("security", sa.String(length=64), nullable=True),
        sa.Column("cusip", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_instrument_cusip"), "instrument", ["cusip"], unique=False)
    op.create_index(
        op.f("ix_instrument_instrument_type"), "instrument", ["instrument_type"], unique=False
    )
    op.create_index(op.f("ix_instrument_issuer_id"), "instrument", ["issuer_id"], unique=False)
    op.create_index(op.f("ix_instrument_maturity"), "instrument", ["maturity"], unique=False)
    op.create_index(op.f("ix_instrument_status"), "instrument", ["status"], unique=False)

    op.create_table(
        "call_terms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), nullable=False),
        sa.Column("par_call_date", sa.Date(), nullable=True),
        sa.Column("make_whole_spread_bps", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id"),
    )

    op.create_table(
        "curated_source",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_curated_source_instrument_id"), "curated_source", ["instrument_id"], unique=False
    )

    op.create_table(
        "filing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=True),
        sa.Column("form_type", sa.String(length=32), nullable=True),
        sa.Column("accession_number", sa.String(length=32), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("filed_at", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession_number"),
    )
    op.create_index(op.f("ix_filing_content_hash"), "filing", ["content_hash"], unique=False)
    op.create_index(op.f("ix_filing_filed_at"), "filing", ["filed_at"], unique=False)
    op.create_index(op.f("ix_filing_form_type"), "filing", ["form_type"], unique=False)
    op.create_index(op.f("ix_filing_issuer_id"), "filing", ["issuer_id"], unique=False)

    op.create_table(
        "event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_filing_id", sa.String(length=36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_filing_id"], ["filing.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_event_event_date"), "event", ["event_date"], unique=False)
    op.create_index(op.f("ix_event_event_type"), "event", ["event_type"], unique=False)
    op.create_index(op.f("ix_event_issuer_id"), "event", ["issuer_id"], unique=False)
    op.create_index(op.f("ix_event_source_filing_id"), "event", ["source_filing_id"], unique=False)

    op.create_table(
        "watchlist",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer_id", name="uq_watchlist_issuer_id"),
    )
    op.create_index(op.f("ix_watchlist_issuer_id"), "watchlist", ["issuer_id"], unique=False)

    op.create_table(
        "state_change",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), nullable=True),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("proposed_diff", JsonType, nullable=False),
        sa.Column("evidence", JsonType, nullable=True),
        sa.Column("verifier_result", JsonType, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_state_change_event_id"), "state_change", ["event_id"], unique=False)
    op.create_index(
        op.f("ix_state_change_instrument_id"), "state_change", ["instrument_id"], unique=False
    )
    op.create_index(op.f("ix_state_change_run_id"), "state_change", ["run_id"], unique=False)
    op.create_index(op.f("ix_state_change_status"), "state_change", ["status"], unique=False)

    op.create_table(
        "alert",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("before_metrics", JsonType, nullable=True),
        sa.Column("after_metrics", JsonType, nullable=True),
        sa.Column("materiality_reasons", JsonType, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_event_id"), "alert", ["event_id"], unique=False)
    op.create_index(op.f("ix_alert_issuer_id"), "alert", ["issuer_id"], unique=False)
    op.create_index(op.f("ix_alert_run_id"), "alert", ["run_id"], unique=False)
    op.create_index(op.f("ix_alert_status"), "alert", ["status"], unique=False)

    op.create_table(
        "metric_snapshot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer_id", sa.String(length=36), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("metrics", JsonType, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["issuer.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_metric_snapshot_as_of"), "metric_snapshot", ["as_of"], unique=False)
    op.create_index(
        op.f("ix_metric_snapshot_issuer_id"), "metric_snapshot", ["issuer_id"], unique=False
    )
    op.create_index(op.f("ix_metric_snapshot_run_id"), "metric_snapshot", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_metric_snapshot_run_id"), table_name="metric_snapshot")
    op.drop_index(op.f("ix_metric_snapshot_issuer_id"), table_name="metric_snapshot")
    op.drop_index(op.f("ix_metric_snapshot_as_of"), table_name="metric_snapshot")
    op.drop_table("metric_snapshot")

    op.drop_index(op.f("ix_alert_status"), table_name="alert")
    op.drop_index(op.f("ix_alert_run_id"), table_name="alert")
    op.drop_index(op.f("ix_alert_issuer_id"), table_name="alert")
    op.drop_index(op.f("ix_alert_event_id"), table_name="alert")
    op.drop_table("alert")

    op.drop_index(op.f("ix_state_change_status"), table_name="state_change")
    op.drop_index(op.f("ix_state_change_run_id"), table_name="state_change")
    op.drop_index(op.f("ix_state_change_instrument_id"), table_name="state_change")
    op.drop_index(op.f("ix_state_change_event_id"), table_name="state_change")
    op.drop_table("state_change")

    op.drop_index(op.f("ix_watchlist_issuer_id"), table_name="watchlist")
    op.drop_table("watchlist")

    op.drop_index(op.f("ix_event_source_filing_id"), table_name="event")
    op.drop_index(op.f("ix_event_issuer_id"), table_name="event")
    op.drop_index(op.f("ix_event_event_type"), table_name="event")
    op.drop_index(op.f("ix_event_event_date"), table_name="event")
    op.drop_table("event")

    op.drop_index(op.f("ix_filing_issuer_id"), table_name="filing")
    op.drop_index(op.f("ix_filing_form_type"), table_name="filing")
    op.drop_index(op.f("ix_filing_filed_at"), table_name="filing")
    op.drop_index(op.f("ix_filing_content_hash"), table_name="filing")
    op.drop_table("filing")

    op.drop_index(op.f("ix_curated_source_instrument_id"), table_name="curated_source")
    op.drop_table("curated_source")

    op.drop_table("call_terms")

    op.drop_index(op.f("ix_instrument_status"), table_name="instrument")
    op.drop_index(op.f("ix_instrument_maturity"), table_name="instrument")
    op.drop_index(op.f("ix_instrument_issuer_id"), table_name="instrument")
    op.drop_index(op.f("ix_instrument_instrument_type"), table_name="instrument")
    op.drop_index(op.f("ix_instrument_cusip"), table_name="instrument")
    op.drop_table("instrument")

    op.drop_index(op.f("ix_issuer_parent_id"), table_name="issuer")
    op.drop_index(op.f("ix_issuer_cik"), table_name="issuer")
    op.drop_table("issuer")
