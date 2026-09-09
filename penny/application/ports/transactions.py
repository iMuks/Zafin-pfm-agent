"""Read access to the customer's enriched transaction history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from penny.domain.models import Transaction


@runtime_checkable
class TransactionRepository(Protocol):
    """The only way any use case reaches transaction data.

    Read-only: Penny is an insights agent, so there is no write method to
    misuse. That is not merely a convention — the *absence* of a mutation path
    is what makes "this agent cannot move money" a structural guarantee rather
    than a prompt instruction.

    Implementations must return transactions in ascending date order. Several
    insights (recurrence cadence, duplicate detection) rely on adjacency in the
    returned sequence, so the ordering is part of the contract, not an
    incidental property of the current adapter.
    """

    def all(self) -> Sequence[Transaction]:
        """Every transaction, oldest first."""
        ...

    def date_bounds(self) -> tuple[date, date]:
        """(earliest, latest) transaction date. Raises if the dataset is empty."""
        ...

    def months(self) -> list[str]:
        """Every calendar month present, as "YYYY-MM", ascending."""
        ...
