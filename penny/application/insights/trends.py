"""Month-over-month comparison.

Answers "how does my spending this month compare to last month" and every
"am I spending more on X" variant.

The one subtlety that matters: comparing a partial month against a complete one
produces a headline that is simply false ("spending down 30%!" when the month
is three days short). The result therefore reports the completeness of both
periods, and the system prompt requires Penny to say so rather than quietly
compare 28 days against 31.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository
from penny.domain.periods import is_month_complete


def _percent_change(before: float, after: float) -> float | None:
    """None rather than infinity when there is no baseline to grow from.

    Going from $0 to $50 is not "infinitely more expensive"; it is new spending,
    and the model should describe it that way.
    """
    return round(100 * (after - before) / before, 1) if before else None


class TrendInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository
        self._selector = TransactionSelector(repository)

    def compare_periods(
        self, month_a: str, month_b: str, category: str | None = None
    ) -> dict[str, Any]:
        _, latest = self._repository.date_bounds()

        def totals(month: str) -> dict[str, float]:
            aggregated: dict[str, float] = defaultdict(float)
            for txn in self._selector.select(month=month, category=category).transactions:
                aggregated[txn.category] += txn.amount
            return aggregated

        a, b = totals(month_a), totals(month_b)
        total_a, total_b = sum(a.values()), sum(b.values())

        changes = [
            {
                "category": name,
                month_a: round(a.get(name, 0.0), 2),
                month_b: round(b.get(name, 0.0), 2),
                "change": round(b.get(name, 0.0) - a.get(name, 0.0), 2),
                "percent_change": _percent_change(a.get(name, 0.0), b.get(name, 0.0)),
            }
            for name in sorted(set(a) | set(b))
        ]
        changes.sort(key=lambda c: -abs(c["change"]))

        a_complete = is_month_complete(month_a, latest)
        b_complete = is_month_complete(month_b, latest)

        return {
            "month_a": month_a,
            "month_b": month_b,
            "category": category,
            "total_a": round(total_a, 2),
            "total_b": round(total_b, 2),
            "change": round(total_b - total_a, 2),
            "percent_change": _percent_change(total_a, total_b),
            "by_category": changes,
            "month_a_complete": a_complete,
            "month_b_complete": b_complete,
            "comparable": a_complete and b_complete,
            "caveat": (
                None
                if a_complete and b_complete
                else (
                    "One of these months is incomplete in the data, so this is not a "
                    "like-for-like comparison. Say so before quoting the percentage."
                )
            ),
        }

    def monthly_totals(self, limit: int = 6) -> dict[str, Any]:
        """Total spend per month — the series behind a trend chart."""
        months = self._repository.months()[-max(1, limit) :]
        return {"months": [{"month": m, "total": self._selector.spend_in_month(m)} for m in months]}
