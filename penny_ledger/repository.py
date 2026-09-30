"""The ledger repository: writes commit atomically at a new version; reads
are pinned, immutable snapshots (design R2, R3, R12).

`commit` is the only way rows enter the ledger. It takes the next
tenant-global version, inserts and retires in one transaction with the sync
run and its audit row, and only after the transaction commits does it publish
`SnapshotCommitted` on the event bus. `snapshot(tenant, version)` returns a
`TransactionRepository` view that the ten insights consume unchanged.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine, and_, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from penny.application.ports.audit import AuditRecord, AuditSink
from penny.application.ports.events import EventBus
from penny.application.ports.transactions import TransactionRepository
from penny.domain.errors import InvalidTransactionError, PennyError
from penny.domain.events import SnapshotCommitted
from penny.domain.ledger import (
    Account,
    Posting,
    Source,
    SyncRun,
    Tenant,
    TransferPair,
    to_transaction,
)
from penny.domain.models import Transaction
from penny_ledger import schema as s


class CommitConflictError(PennyError):
    """Two commits raced for the same tenant version; the caller retries."""

    status_code = 503


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _active(table, version: int):
    return and_(
        table.c.created_version <= version,
        or_(table.c.retired_version.is_(None), table.c.retired_version > version),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Batch:
    """What one sync wants committed. Ids of rows to retire refer to active rows."""

    source_id: str
    postings: list[Posting] = field(default_factory=list)
    retire_posting_ids: list[str] = field(default_factory=list)
    transfer_pairs: list[TransferPair] = field(default_factory=list)
    retire_pair_ids: list[str] = field(default_factory=list)
    coverage_through: date | None = None
    run: SyncRun | None = None


class SnapshotView(TransactionRepository):
    """Immutable: the rows active at one version, projected to Transaction."""

    def __init__(self, tenant_id: str, version: int, transactions: Sequence[Transaction]) -> None:
        self.tenant_id = tenant_id
        self.version = version
        self._transactions = tuple(sorted(transactions, key=lambda t: (t.day, t.id)))
        self._months: list[str] | None = None

    def all(self) -> Sequence[Transaction]:
        return self._transactions

    def date_bounds(self) -> tuple[date, date]:
        if not self._transactions:
            raise InvalidTransactionError("The snapshot is empty.")
        return self._transactions[0].day, self._transactions[-1].day

    def months(self) -> list[str]:
        if self._months is None:
            self._months = sorted({t.month for t in self._transactions})
        return list(self._months)


class LedgerRepository:
    def __init__(self, engine: Engine, *, events: EventBus, cache_size: int = 64) -> None:
        self._engine = engine
        self._events = events
        self._cache: OrderedDict[tuple[str, int], SnapshotView] = OrderedDict()
        self._cache_size = cache_size

    # -- provisioning ---------------------------------------------------------

    def provision_tenant(self, tenant: Tenant) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.tenants).values(
                    id=tenant.id,
                    name=tenant.name,
                    contact_name=tenant.contact_name,
                    contact_email=tenant.contact_email,
                    reporting_currency=tenant.reporting_currency,
                    timezone=tenant.timezone,
                )
            )

    def add_account(self, account: Account) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.accounts).values(
                    id=account.id,
                    tenant_id=account.tenant_id,
                    name=account.name,
                    type=account.type,
                    currency=account.currency,
                    side=account.side,
                )
            )

    def add_source(self, source: Source) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.sources).values(
                    id=source.id,
                    tenant_id=source.tenant_id,
                    kind=source.kind,
                    credentials_ref=source.credentials_ref,
                    state=source.state,
                    coverage_through=source.coverage_through,
                    external_ref=source.external_ref,
                )
            )

    def attach(
        self, tenant_id: str, account_id: str, source_id: str, external_id: str | None = None
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.account_sources).values(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    source_id=source_id,
                    external_id=external_id,
                    attached_version=self._latest(conn, tenant_id),
                )
            )

    def set_source_state(self, tenant_id: str, source_id: str, state: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(s.sources)
                .where(s.sources.c.tenant_id == tenant_id, s.sources.c.id == source_id)
                .values(state=state)
            )

    def source(self, tenant_id: str, source_id: str) -> Source | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(s.sources).where(
                        s.sources.c.tenant_id == tenant_id, s.sources.c.id == source_id
                    )
                )
                .mappings()
                .first()
            )
        return Source(**row) if row else None

    def accounts(self, tenant_id: str) -> list[Account]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.accounts).where(s.accounts.c.tenant_id == tenant_id)
            ).mappings()
            return [Account(**r) for r in rows]

    # -- versions ---------------------------------------------------------------

    @staticmethod
    def _latest(conn, tenant_id: str) -> int:
        value = conn.execute(
            select(func.max(s.sync_versions.c.version)).where(
                s.sync_versions.c.tenant_id == tenant_id
            )
        ).scalar()
        return int(value or 0)

    def latest_version(self, tenant_id: str) -> int:
        with self._engine.connect() as conn:
            return self._latest(conn, tenant_id)

    def active_row_hashes(self, tenant_id: str, source_id: str) -> set[str]:
        with self._engine.connect() as conn:
            version = self._latest(conn, tenant_id)
            rows = conn.execute(
                select(s.postings.c.row_hash).where(
                    s.postings.c.tenant_id == tenant_id,
                    s.postings.c.source_id == source_id,
                    _active(s.postings, version),
                )
            )
            return {r[0] for r in rows}

    # -- the commit -------------------------------------------------------------

    def commit(self, tenant_id: str, batch: Batch) -> SyncRun:
        """All rows or none, at the next version, with the run and its audit row."""
        for posting in batch.postings:
            if posting.tenant_id != tenant_id:
                raise InvalidTransactionError(
                    "a posting from another tenant cannot be committed here"
                )
        started = _utcnow()
        try:
            with self._engine.begin() as conn:
                version = self._latest(conn, tenant_id) + 1
                conn.execute(
                    insert(s.sync_versions).values(
                        tenant_id=tenant_id,
                        version=version,
                        committed_at=started,
                        source_id=batch.source_id,
                    )
                )
                if batch.retire_posting_ids:
                    conn.execute(
                        update(s.postings)
                        .where(
                            s.postings.c.tenant_id == tenant_id,
                            s.postings.c.id.in_(batch.retire_posting_ids),
                            s.postings.c.retired_version.is_(None),
                        )
                        .values(retired_version=version)
                    )
                    # A retired posting takes its transfer pair with it (Supersession).
                    conn.execute(
                        update(s.transfer_pairs)
                        .where(
                            s.transfer_pairs.c.tenant_id == tenant_id,
                            s.transfer_pairs.c.retired_version.is_(None),
                            or_(
                                s.transfer_pairs.c.posting_a.in_(batch.retire_posting_ids),
                                s.transfer_pairs.c.posting_b.in_(batch.retire_posting_ids),
                            ),
                        )
                        .values(retired_version=version)
                    )
                if batch.retire_pair_ids:
                    conn.execute(
                        update(s.transfer_pairs)
                        .where(
                            s.transfer_pairs.c.tenant_id == tenant_id,
                            s.transfer_pairs.c.id.in_(batch.retire_pair_ids),
                            s.transfer_pairs.c.retired_version.is_(None),
                        )
                        .values(retired_version=version)
                    )
                if batch.postings:
                    conn.execute(
                        insert(s.postings),
                        [
                            {
                                "id": p.id,
                                "tenant_id": p.tenant_id,
                                "account_id": p.account_id,
                                "source_id": p.source_id,
                                "day": p.day,
                                "amount": p.amount,
                                "direction": p.direction,
                                "description": p.description,
                                "merchant": p.merchant,
                                "category": p.category,
                                "confidence": p.confidence,
                                "row_hash": p.row_hash,
                                "native_id": p.native_id,
                                "status": p.status,
                                "supersedes_id": p.supersedes_id,
                                "created_version": version,
                                "retired_version": None,
                                "is_recurring": p.is_recurring,
                                "logo_domain": p.logo_domain,
                                "provenance": p.provenance,
                            }
                            for p in batch.postings
                        ],
                    )
                if batch.transfer_pairs:
                    conn.execute(
                        insert(s.transfer_pairs),
                        [
                            {
                                "id": t.id,
                                "tenant_id": tenant_id,
                                "posting_a": t.posting_a,
                                "posting_b": t.posting_b,
                                "created_version": version,
                                "retired_version": None,
                            }
                            for t in batch.transfer_pairs
                        ],
                    )
                if batch.coverage_through is not None:
                    conn.execute(
                        update(s.sources)
                        .where(
                            s.sources.c.tenant_id == tenant_id, s.sources.c.id == batch.source_id
                        )
                        .values(coverage_through=batch.coverage_through)
                    )

                base = batch.run or SyncRun(tenant_id=tenant_id, source_id=batch.source_id)
                run = SyncRun(
                    id=base.id,
                    tenant_id=tenant_id,
                    source_id=batch.source_id,
                    sync_version=version,
                    started=started,
                    finished=_utcnow(),
                    rows_in=len(batch.postings),
                    rows_rejected=base.rows_rejected,
                    rows_skipped=base.rows_skipped,
                    rows_deduped=base.rows_deduped,
                    rows_retired=len(batch.retire_posting_ids),
                    balance_check=base.balance_check,
                    transfer_pairs_formed=len(batch.transfer_pairs),
                    warnings=base.warnings,
                    status="committed",
                )
                conn.execute(
                    insert(s.sync_runs).values(
                        id=run.id,
                        tenant_id=tenant_id,
                        source_id=run.source_id,
                        sync_version=version,
                        started=run.started,
                        finished=run.finished,
                        rows_in=run.rows_in,
                        rows_rejected=run.rows_rejected,
                        rows_skipped=run.rows_skipped,
                        rows_deduped=run.rows_deduped,
                        rows_retired=run.rows_retired,
                        balance_check=run.balance_check,
                        transfer_pairs_formed=run.transfer_pairs_formed,
                        warnings=json.dumps(list(run.warnings)),
                        status=run.status,
                    )
                )
                # The audit row is part of the same transaction (design R3).
                conn.execute(
                    insert(s.audit_events).values(
                        id=uuid4().hex,
                        tenant_id=tenant_id,
                        kind="sync_committed",
                        occurred_at=run.finished,
                        reference=run.id,
                        payload=json.dumps(
                            {
                                "source_id": run.source_id,
                                "sync_version": version,
                                "rows_in": run.rows_in,
                                "rows_retired": run.rows_retired,
                                "balance_check": run.balance_check,
                            }
                        ),
                    )
                )
        except IntegrityError as exc:
            raise CommitConflictError("another commit took this version; retry") from exc

        self._events.publish(
            SnapshotCommitted(
                tenant_id=tenant_id,
                version=version,
                source_id=batch.source_id,
                coverage_through=batch.coverage_through,
                rows_in=run.rows_in,
                rows_retired=run.rows_retired,
            )
        )
        return run

    # -- the read view ---------------------------------------------------------

    def snapshot(self, tenant_id: str, version: int | None = None) -> SnapshotView:
        """The rows active at `version` (latest when None), cached per (tenant, version)."""
        with self._engine.connect() as conn:
            if version is None:
                version = self._latest(conn, tenant_id)
            key = (tenant_id, version)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

            bank_side = select(s.accounts.c.id).where(
                s.accounts.c.tenant_id == tenant_id, s.accounts.c.type.in_(("bank", "card"))
            )
            rows = (
                conn.execute(
                    select(s.postings).where(
                        s.postings.c.tenant_id == tenant_id,
                        s.postings.c.account_id.in_(bank_side),
                        _active(s.postings, version),
                    )
                )
                .mappings()
                .all()
            )
            pairs = conn.execute(
                select(s.transfer_pairs.c.posting_a, s.transfer_pairs.c.posting_b).where(
                    s.transfer_pairs.c.tenant_id == tenant_id, _active(s.transfer_pairs, version)
                )
            ).all()
        in_transfer = {p for pair in pairs for p in pair}
        transactions = [
            to_transaction(Posting(**dict(row)), in_transfer=row["id"] in in_transfer)
            for row in rows
        ]
        view = SnapshotView(tenant_id, version, transactions)
        self._cache[key] = view
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return view

    def runs(self, tenant_id: str, limit: int = 10) -> list[dict]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.sync_runs)
                .where(s.sync_runs.c.tenant_id == tenant_id)
                .order_by(s.sync_runs.c.started.desc())
                .limit(limit)
            ).mappings()
            return [dict(r) for r in rows]


class LedgerAuditSink(AuditSink):
    """`AuditSink` over `audit_events`; chat turns and events land in the same table."""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def record(self, entry: AuditRecord) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(s.audit_events).values(
                        id=uuid4().hex,
                        tenant_id=self._tenant_id,
                        kind=entry.kind,
                        occurred_at=_utcnow(),
                        request_id=entry.request_id,
                        session_id=entry.session_id,
                        poid=entry.poid,
                        reference=entry.reference,
                        payload=json.dumps(
                            {
                                "client_query": entry.client_query,
                                "ai_response": entry.ai_response,
                                "tools_called": list(entry.tools_called),
                                "model_id": entry.model_id,
                                "prompt_version": entry.prompt_version,
                            }
                        ),
                    )
                )
        except Exception:  # audit must never break the customer's turn
            from penny.infrastructure.observability.telemetry import log

            log("audit.write_failed", kind=entry.kind)

    def audit_kinds(self) -> list[str]:
        with self._engine.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    select(s.audit_events.c.kind)
                    .where(s.audit_events.c.tenant_id == self._tenant_id)
                    .order_by(s.audit_events.c.occurred_at)
                )
            ]


def create_schema(engine: Engine) -> None:
    """Fixtures and tests. Deployments run Alembic (`penny_ledger/migrations`)."""
    s.metadata.create_all(engine)


__all__ = [
    "Batch",
    "CommitConflictError",
    "LedgerAuditSink",
    "LedgerRepository",
    "SnapshotView",
    "create_schema",
    "Decimal",
]
