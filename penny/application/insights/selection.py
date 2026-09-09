"""The shared filter step every insight starts from.

Pulled out because "which transactions are we talking about" was being answered
slightly differently in each aggregation, and the differences were invisible.
One selector means one definition of "spending in July on Groceries" — and one
place to change it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from penny.application.ports.transactions import TransactionRepository
from penny.domain.models import Transaction
from penny.domain.periods import Period
from penny.domain.taxonomy import canonical


@dataclass(frozen=True, slots=True)
class Selection:
    """A resolved set of transactions plus the period they were selected over.

    The period travels with the rows because averages need a denominator that
    the rows themselves cannot supply: three grocery runs tell you nothing
    about whether they span one week or three months.
    """

    transactions: tuple[Transaction, ...]
    period: Period
    month: str | None

    @property
    def total(self) -> float:
        return round(sum(t.amount for t in self.transactions), 2)

    @property
    def count(self) -> int:
        return len(self.transactions)

    @property
    def period_label(self) -> str:
        return self.month or "all data"

    @property
    def average_transaction(self) -> float:
        return round(self.total / self.count, 2) if self.count else 0.0

    @property
    def average_per_week(self) -> float:
        """Averaged over the calendar period, not over the days that had spend.

        Asking "what's my average weekly grocery spend" in a month with two
        shopping trips three days apart must not answer "half of that in a
        week". The denominator is the month.
        """
        return round(self.total / self.period.weeks, 2) if self.count else 0.0


class TransactionSelector:
    """Resolves (month, category, merchant) into a `Selection`."""

    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository

    def coverage_period(self) -> Period:
        first, last = self._repository.date_bounds()
        return Period.spanning(first, last)

    def resolve_period(self, month: str | None) -> Period:
        return Period.for_month(month) if month else self.coverage_period()

    def select(
        self,
        *,
        month: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        spend_only: bool = True,
        min_amount: float | None = None,
    ) -> Selection:
        """Filter the history.

        `spend_only` defaults to True because almost every question a customer
        asks is about consumption. The exceptions — income ratios, coverage —
        opt out explicitly, which makes those call sites easy to audit.

        An unrecognised `category` yields an EMPTY selection, never an error and
        never "all categories". That distinction is the whole point: falling
        through to no filter would make a question about a category that does
        not exist return the customer's entire spend, reported as if it were
        that category. Zero is wrong but obviously wrong; total spend is wrong
        and plausible, which is far worse.
        """
        period = self.resolve_period(month)

        wanted: str | None = None
        if category is not None:
            wanted = canonical(category)
            if wanted is None:
                return Selection(transactions=(), period=period, month=month)

        rows: list[Transaction] = []
        for txn in self._repository.all():
            if spend_only and not txn.is_spend:
                continue
            if month is not None and txn.month != month:
                continue
            if wanted is not None and txn.category != wanted:
                continue
            if merchant and not txn.matches_merchant(merchant):
                continue
            if min_amount is not None and txn.amount < min_amount:
                continue
            rows.append(txn)

        return Selection(transactions=tuple(rows), period=period, month=month)

    def spend_in_month(self, month: str) -> float:
        return self.select(month=month).total


def bounded_period(period: Period, latest: date) -> Period:
    """Clip a calendar period to the data actually available.

    Used where a rate is computed: projecting July from a 31-day denominator
    when the data stops on the 28th would understate the run rate.
    """
    return Period(period.start, min(period.end, latest)) if latest < period.end else period
