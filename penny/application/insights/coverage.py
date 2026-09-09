"""What data exists — and, more importantly, what does not.

This is the honesty backstop. Several of the assignment's user stories ask for
things transaction history cannot supply (an account balance, a savings goal, a
budget). The system prompt tells Penny to check here first when a question
smells like one of those, so that "I don't have that" is grounded in a tool
result rather than left to the model's discretion.
"""

from __future__ import annotations

from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository
from penny.domain.periods import is_month_complete, last_complete_month

#: Stated in the tool result rather than only in the prompt. A caveat that
#: travels with the data cannot be dropped by a model that is paraphrasing.
NOT_IN_DATASET = (
    "This dataset is transaction history only. It contains no account balance, "
    "no credit limit, no budget, no savings goal, and no scheduled-bill calendar. "
    "Any question needing one of those cannot be answered from this data."
)


class CoverageInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository
        self._selector = TransactionSelector(repository)

    def data_coverage(self) -> dict[str, Any]:
        first, latest = self._repository.date_bounds()
        months = self._repository.months()
        spend = [t for t in self._repository.all() if t.is_spend]
        complete = last_complete_month(months, latest)

        return {
            "first_date": first.isoformat(),
            "latest_date": latest.isoformat(),
            # Anchoring "today" to the data, not the wall clock. The sample ends
            # 2026-07-28; resolving "this month" against the real current date
            # would return nothing and read as a bug rather than as empty data.
            "treat_as_today": latest.isoformat(),
            "months": months,
            "current_month": months[-1] if months else None,
            "last_complete_month": complete,
            "partial_month": None
            if not months or is_month_complete(months[-1], latest)
            else months[-1],
            "transaction_count": len(self._repository.all()),
            "categories_present": sorted({t.category for t in spend}),
            "merchants_present": len({t.merchant for t in spend}),
            "total_spend": round(sum(t.amount for t in spend), 2),
            "note": NOT_IN_DATASET,
        }
