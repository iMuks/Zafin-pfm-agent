"""The ledger schema, SQLAlchemy Core. Every table carries tenant_id."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from penny_ledger.types import Money

metadata = MetaData()

tenants = Table(
    "tenants",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("contact_name", String(200), nullable=False),
    Column("contact_email", String(320), nullable=False),
    Column("reporting_currency", String(3), nullable=False),
    Column("timezone", String(64), nullable=False),
)

accounts = Table(
    "accounts",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False, index=True),
    Column("name", String(200), nullable=False),
    Column("type", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("side", String(8), nullable=False),
)

sources = Table(
    "sources",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False, index=True),
    Column("kind", String(16), nullable=False),
    Column("credentials_ref", String(200)),
    Column("state", String(16), nullable=False),
    Column("coverage_through", Date),
    Column("external_ref", String(200)),
)

account_sources = Table(
    "account_sources",
    metadata,
    Column("tenant_id", String(32), nullable=False),
    Column("account_id", String(32), nullable=False),
    Column("source_id", String(32), nullable=False),
    Column("external_id", String(200)),
    Column("attached_version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "account_id", "source_id", name="uq_account_source"),
)

#: One row per commit; the version is tenant-global and monotonic.
sync_versions = Table(
    "sync_versions",
    metadata,
    Column("tenant_id", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("committed_at", DateTime(timezone=True), nullable=False),
    Column("source_id", String(32), nullable=False),
    UniqueConstraint("tenant_id", "version", name="uq_sync_version"),
)

postings = Table(
    "postings",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False),
    Column("account_id", String(32), nullable=False),
    Column("source_id", String(32), nullable=False),
    Column("day", Date, nullable=False),
    Column("amount", Money, nullable=False),
    Column("direction", String(6), nullable=False),
    Column("description", Text, nullable=False),
    Column("merchant", String(200), nullable=False),
    Column("category", String(64), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("row_hash", String(128), nullable=False),
    Column("native_id", String(200)),
    Column("status", String(8), nullable=False),
    Column("supersedes_id", String(32)),
    Column("created_version", Integer, nullable=False),
    Column("retired_version", Integer),
    Column("is_recurring", Boolean, nullable=False, default=False),
    Column("logo_domain", String(200)),
    Column("provenance", Text),
    Index("ix_postings_snapshot", "tenant_id", "created_version", "retired_version"),
    Index("ix_postings_account_day", "tenant_id", "account_id", "day"),
    Index("ix_postings_row_hash", "tenant_id", "source_id", "row_hash"),
)

transfer_pairs = Table(
    "transfer_pairs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False),
    Column("posting_a", String(32), nullable=False),
    Column("posting_b", String(32), nullable=False),
    Column("created_version", Integer, nullable=False),
    Column("retired_version", Integer),
    Index("ix_transfer_pairs_snapshot", "tenant_id", "created_version", "retired_version"),
)

sync_runs = Table(
    "sync_runs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False, index=True),
    Column("source_id", String(32), nullable=False),
    Column("sync_version", Integer),
    Column("started", DateTime(timezone=True)),
    Column("finished", DateTime(timezone=True)),
    Column("rows_in", Integer, nullable=False, default=0),
    Column("rows_rejected", Integer, nullable=False, default=0),
    Column("rows_skipped", Integer, nullable=False, default=0),
    Column("rows_deduped", Integer, nullable=False, default=0),
    Column("rows_retired", Integer, nullable=False, default=0),
    Column("balance_check", String(16), nullable=False),
    Column("transfer_pairs_formed", Integer, nullable=False, default=0),
    Column("warnings", Text, nullable=False, default="[]"),
    Column("status", String(16), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tenant_id", String(32), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("request_id", String(64)),
    Column("session_id", String(64)),
    Column("poid", String(128)),
    Column("reference", String(200)),
    Column("payload", Text, nullable=False, default="{}"),
    Index("ix_audit_events_kind", "tenant_id", "kind", "occurred_at"),
)
