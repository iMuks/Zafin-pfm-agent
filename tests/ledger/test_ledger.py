"""The ledger's promises, on SQLite in memory.

Exact money to the cent, rows never updated in place, one tenant never sees
another, a commit is all-or-nothing, and every commit is announced as an event.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine

from penny.application.ports.audit import AuditRecord
from penny.domain.errors import InvalidTransactionError
from penny.domain.events import SnapshotCommitted
from penny.domain.ledger import (
    Account,
    Posting,
    Source,
    SyncRun,
    Tenant,
    TransferPair,
    balance_identity,
    money,
)
from penny.infrastructure.events.memory import InMemoryEventBus
from penny_ledger.repository import (
    Batch,
    CommitConflictError,
    LedgerAuditSink,
    LedgerRepository,
    create_schema,
)

T1, T2 = "tenant1", "tenant2"


def posting(tenant, account, day, amount, direction="debit", **kw) -> Posting:
    defaults = {
        "tenant_id": tenant,
        "account_id": account,
        "source_id": kw.pop("source_id", "src1"),
        "day": date.fromisoformat(day),
        "amount": money(amount),
        "direction": direction,
        "description": kw.pop("description", "POS PURCHASE"),
        "merchant": kw.pop("merchant", "Shop"),
        "category": kw.pop("category", "Groceries"),
        "row_hash": kw.pop("row_hash", f"{account}:{day}:{amount}"),
    }
    defaults.update(kw)
    return Posting(**defaults)


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        create_schema(self.engine)
        self.bus = InMemoryEventBus()
        self.ledger = LedgerRepository(self.engine, events=self.bus, cache_size=2)
        for tenant in (T1, T2):
            self.ledger.provision_tenant(
                Tenant(id=tenant, name=tenant, contact_name="c", contact_email="c@x")
            )
            self.ledger.add_source(
                Source(id="src1", tenant_id=tenant, kind="aggregator")
                if tenant == T1
                else Source(id="src2", tenant_id=tenant, kind="file")
            )
        self.ledger.add_account(
            Account(id="chq", tenant_id=T1, name="Chequing", type="bank", currency="CAD")
        )
        self.ledger.add_account(
            Account(id="card", tenant_id=T1, name="Visa", type="card", currency="CAD")
        )
        self.ledger.add_account(
            Account(id="chq2", tenant_id=T2, name="Other", type="bank", currency="CAD")
        )


class Money(unittest.TestCase):
    def test_money_is_exact_from_strings_ints_and_two_decimal_floats(self):
        self.assertEqual(money("0.1") + money("0.2"), Decimal("0.30"))
        self.assertEqual(money(0.1) + money(0.2), Decimal("0.30"))
        self.assertEqual(money(5), Decimal("5.00"))

    def test_money_rejects_nonsense(self):
        for bad in ("abc", float("nan"), float("inf"), None):
            with self.assertRaises(InvalidTransactionError):
                money(bad)

    def test_balance_identity_holds_to_the_cent_over_many_rows(self):
        rows = [posting(T1, "chq", "2026-06-01", "0.10") for _ in range(1000)]
        self.assertEqual(
            balance_identity(Decimal("1000.00"), rows, Decimal("900.00")), Decimal("0.00")
        )
        self.assertEqual(
            balance_identity(Decimal("1000.00"), rows, Decimal("900.01")), Decimal("-0.01")
        )


class Commit(LedgerCase):
    def test_a_commit_takes_the_next_version_and_announces_itself(self):
        run = self.ledger.commit(
            T1,
            Batch(
                source_id="src1",
                postings=[posting(T1, "chq", "2026-06-01", "12.34")],
                coverage_through=date(2026, 6, 1),
            ),
        )
        self.assertEqual((run.sync_version, run.rows_in, run.status), (1, 1, "committed"))
        self.assertEqual(self.ledger.latest_version(T1), 1)
        events = self.bus.of(SnapshotCommitted)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0].tenant_id, events[0].version, events[0].coverage_through),
            (T1, 1, date(2026, 6, 1)),
        )
        self.assertEqual(self.ledger.source(T1, "src1").coverage_through, date(2026, 6, 1))

    def test_versions_are_per_tenant_and_monotonic(self):
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-01", "1.00")])
        )
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-02", "2.00")])
        )
        self.ledger.commit(
            T2,
            Batch(
                source_id="src2",
                postings=[posting(T2, "chq2", "2026-06-01", "9.00", source_id="src2")],
            ),
        )
        self.assertEqual((self.ledger.latest_version(T1), self.ledger.latest_version(T2)), (2, 1))

    def test_a_posting_from_another_tenant_cannot_be_committed(self):
        with self.assertRaises(InvalidTransactionError):
            self.ledger.commit(
                T1, Batch(source_id="src1", postings=[posting(T2, "chq2", "2026-06-01", "1.00")])
            )
        self.assertEqual(self.ledger.latest_version(T1), 0)

    def test_commit_is_all_or_nothing(self):
        good = posting(T1, "chq", "2026-06-01", "1.00")
        duplicate_id = posting(T1, "chq", "2026-06-02", "2.00", id=good.id)  # primary key clash
        with self.assertRaises(CommitConflictError):
            self.ledger.commit(T1, Batch(source_id="src1", postings=[good, duplicate_id]))
        self.assertEqual(self.ledger.latest_version(T1), 0)
        self.assertEqual(self.ledger.runs(T1), [])
        self.assertEqual(self.bus.of(SnapshotCommitted), [])

    def test_the_sync_run_and_its_audit_row_land_in_the_same_commit(self):
        self.ledger.commit(
            T1,
            Batch(
                source_id="src1",
                postings=[posting(T1, "chq", "2026-06-01", "1.00")],
                run=SyncRun(tenant_id=T1, source_id="src1", balance_check="held", rows_skipped=2),
            ),
        )
        run = self.ledger.runs(T1)[0]
        self.assertEqual(
            (run["balance_check"], run["rows_skipped"], run["sync_version"]), ("held", 2, 1)
        )
        self.assertEqual(LedgerAuditSink(self.engine, T1).audit_kinds(), ["sync_committed"])


class Snapshot(LedgerCase):
    def test_money_survives_the_round_trip_exactly(self):
        self.ledger.commit(
            T1,
            Batch(
                source_id="src1",
                postings=[posting(T1, "chq", "2026-06-01", "0.10") for _ in range(3)]
                + [posting(T1, "chq", "2026-06-02", "1234567.89")],
            ),
        )
        amounts = sorted(t.amount for t in self.ledger.snapshot(T1).all())
        self.assertEqual(amounts, [0.1, 0.1, 0.1, 1234567.89])
        self.assertEqual(sum(Decimal(str(a)) for a in amounts), Decimal("1234568.19"))

    def test_one_tenant_never_sees_another(self):
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-01", "1.00")])
        )
        self.ledger.commit(
            T2,
            Batch(
                source_id="src2",
                postings=[posting(T2, "chq2", "2026-06-01", "9.00", source_id="src2")],
            ),
        )
        self.assertEqual([t.amount for t in self.ledger.snapshot(T1).all()], [1.0])
        self.assertEqual([t.amount for t in self.ledger.snapshot(T2).all()], [9.0])
        self.assertEqual(self.ledger.snapshot("nobody").all(), ())

    def test_a_retired_row_is_gone_at_the_new_version_and_present_at_the_old(self):
        old = posting(T1, "chq", "2026-06-01", "5.00")
        self.ledger.commit(T1, Batch(source_id="src1", postings=[old]))
        new = posting(T1, "chq", "2026-06-01", "5.50", supersedes_id=old.id, row_hash="corrected")
        self.ledger.commit(T1, Batch(source_id="src1", postings=[new], retire_posting_ids=[old.id]))
        self.assertEqual([t.amount for t in self.ledger.snapshot(T1, 1).all()], [5.0])
        self.assertEqual([t.amount for t in self.ledger.snapshot(T1, 2).all()], [5.5])
        self.assertEqual([t.amount for t in self.ledger.snapshot(T1).all()], [5.5])
        self.assertEqual(self.ledger.runs(T1)[0]["rows_retired"], 1)

    def test_a_transfer_pair_reads_as_transfers_without_touching_the_stored_category(self):
        out = posting(T1, "chq", "2026-06-03", "200.00", category="Shopping")
        back = posting(T1, "card", "2026-06-03", "200.00", direction="credit", category="Shopping")
        self.ledger.commit(
            T1,
            Batch(
                source_id="src1",
                postings=[out, back],
                transfer_pairs=[TransferPair(tenant_id=T1, posting_a=out.id, posting_b=back.id)],
            ),
        )
        categories = {t.id: t.category for t in self.ledger.snapshot(T1).all()}
        self.assertEqual(categories, {out.id: "Transfers", back.id: "Transfers"})
        self.assertFalse(any(t.is_spend for t in self.ledger.snapshot(T1).all()))
        # Retiring one side retires the pair; the survivor is spend again.
        self.ledger.commit(T1, Batch(source_id="src1", retire_posting_ids=[back.id]))
        self.assertEqual([t.category for t in self.ledger.snapshot(T1).all()], ["Shopping"])

    def test_snapshot_serves_the_port_contract(self):
        self.ledger.commit(
            T1,
            Batch(
                source_id="src1",
                postings=[
                    posting(T1, "chq", "2026-07-02", "1.00"),
                    posting(T1, "chq", "2026-05-31", "2.00"),
                ],
            ),
        )
        view = self.ledger.snapshot(T1)
        self.assertEqual([t.day.isoformat() for t in view.all()], ["2026-05-31", "2026-07-02"])
        self.assertEqual(view.date_bounds(), (date(2026, 5, 31), date(2026, 7, 2)))
        self.assertEqual(view.months(), ["2026-05", "2026-07"])
        with self.assertRaises(InvalidTransactionError):
            self.ledger.snapshot(T2).date_bounds()

    def test_snapshots_are_pinned_immutable_and_cached_with_a_bound(self):
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-01", "1.00")])
        )
        v1 = self.ledger.snapshot(T1)
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-02", "2.00")])
        )
        self.assertIs(self.ledger.snapshot(T1, 1), v1)  # cache hit, same object
        self.assertEqual(len(v1.all()), 1)  # pinned: the later commit did not leak in
        self.assertEqual(len(self.ledger.snapshot(T1).all()), 2)
        self.ledger.commit(
            T1, Batch(source_id="src1", postings=[posting(T1, "chq", "2026-06-03", "3.00")])
        )
        self.ledger.snapshot(T1)  # three versions seen, cache holds two
        self.assertIsNot(self.ledger.snapshot(T1, 1), v1)  # evicted, rebuilt


class AuditSink(LedgerCase):
    def test_chat_turns_and_events_share_the_table_and_the_tenant(self):
        sink = LedgerAuditSink(self.engine, T1)
        sink.record(
            AuditRecord(
                request_id="r1",
                session_id="s1",
                poid="p",
                client_query="q",
                ai_response="a",
                tools_called=("get_top_categories",),
                model_id="m",
                prompt_version="v1",
            )
        )
        sink.record(
            AuditRecord(
                request_id="r2",
                session_id="s1",
                poid="p",
                client_query="",
                ai_response="",
                tools_called=(),
                model_id="m",
                prompt_version="v1",
                kind="guardrail_blocked",
                reference="req-9",
            )
        )
        self.assertEqual(sink.audit_kinds(), ["chat_turn", "guardrail_blocked"])
        self.assertEqual(LedgerAuditSink(self.engine, T2).audit_kinds(), [])
