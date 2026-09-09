"""`TransactionRepository` backed by the enriched JSON file.

A real deployment queries the bank's ledger. The point of the port is that
swapping this class for a warehouse client changes one line in the composition
root and nothing else — no use case, no tool, no prompt.

Two guarantees this adapter owns, because the port promises them and the
insights layer relies on them:

* **Ascending date order.** Recurrence cadence and duplicate detection both walk
  adjacent pairs. Unsorted input would not error; it would quietly produce
  negative day-gaps and wrong answers.
* **Validated records.** Enrichment is an LLM pass, so its output is checked at
  load rather than trusted. A bad category fails here, loudly, as one bad row in
  a file — not silently, as a total the customer is later shown.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from penny.application.ports.transactions import TransactionRepository
from penny.domain.errors import DataNotEnrichedError, InvalidTransactionError
from penny.domain.models import Transaction


class JsonTransactionRepository(TransactionRepository):
    """Loads once, lazily, then serves from memory.

    Lazy rather than at import time so that importing the app never touches the
    filesystem — which is what lets the component gallery and the unit tests run
    against a fixture, or against nothing at all.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._transactions: tuple[Transaction, ...] | None = None
        self._months: list[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> tuple[Transaction, ...]:
        if self._transactions is not None:
            return self._transactions

        if not self._path.exists():
            raise DataNotEnrichedError(
                f"{self._path.name} not found. Run Task 2 first:\n"
                f"    python scripts/enrich_transactions.py"
            )

        try:
            records = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DataNotEnrichedError(f"{self._path.name} is not valid JSON: {exc}") from exc

        if not isinstance(records, list) or not records:
            raise DataNotEnrichedError(f"{self._path.name} contains no transactions.")

        parsed = [Transaction.from_record(record) for record in records]
        parsed.sort(key=lambda t: (t.day, t.id))
        self._transactions = tuple(parsed)
        return self._transactions

    # -- TransactionRepository -------------------------------------------

    def all(self) -> Sequence[Transaction]:
        return self._load()

    def date_bounds(self) -> tuple[date, date]:
        transactions = self._load()
        return transactions[0].day, transactions[-1].day

    def months(self) -> list[str]:
        if self._months is None:
            self._months = sorted({t.month for t in self._load()})
        return list(self._months)

    # -- test / gallery support ------------------------------------------

    def reset(self) -> None:
        """Drop the memoised dataset. Used when a test swaps the file underneath."""
        self._transactions = None
        self._months = None


class InMemoryTransactionRepository(TransactionRepository):
    """A list of `Transaction` objects, for unit tests.

    Exists so that an insight can be tested against four hand-written
    transactions with no file, no JSON and no fixture to keep in sync.
    """

    def __init__(self, transactions: Sequence[Transaction]) -> None:
        self._transactions = tuple(sorted(transactions, key=lambda t: (t.day, t.id)))

    def all(self) -> Sequence[Transaction]:
        return self._transactions

    def date_bounds(self) -> tuple[date, date]:
        if not self._transactions:
            raise InvalidTransactionError("The repository is empty.")
        return self._transactions[0].day, self._transactions[-1].day

    def months(self) -> list[str]:
        return sorted({t.month for t in self._transactions})
