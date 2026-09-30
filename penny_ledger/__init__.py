"""penny_ledger: the versioned, tenant-scoped ledger behind Penny.

Owns the schema, the migrations, the write repository ingestion commits
through, and the read view chat pins to. Depends on `penny.domain`,
`penny.application.ports` and the database driver only.
"""
