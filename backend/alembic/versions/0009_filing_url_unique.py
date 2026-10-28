"""filing.url unique; press releases may omit accession_number

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28 15:45:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # accession_number remains nullable + unique (Postgres/SQLite allow multiple NULLs).
    # Press releases use form_type='press_release' with accession_number NULL; upsert on url.
    op.create_unique_constraint("uq_filing_url", "filing", ["url"])


def downgrade() -> None:
    op.drop_constraint("uq_filing_url", "filing", type_="unique")
