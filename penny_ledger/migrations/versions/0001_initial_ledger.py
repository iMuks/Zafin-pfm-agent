"""Initial ledger: tenants, accounts, sources, postings, transfer pairs,
sync versions, sync runs, audit events. Both engines.

Revision ID: 0001
Revises: None
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

from penny_ledger.schema import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The schema module is the single definition; the initial revision applies it.
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
