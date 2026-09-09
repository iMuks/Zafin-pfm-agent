"""Category spending — the most-asked family of questions.

Answers "how much did I spend on X", "where is my money going", and
"what's my average weekly grocery spend".
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository


class SpendingInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._selector = TransactionSelector(repository)

    def by_category(self, category: str | None = None, month: str | None = None) -> dict[str, Any]:
        selection = self._selector.select(month=month, category=category)

        by_merchant: dict[str, float] = defaultdict(float)
        for txn in selection.transactions:
            by_merchant[txn.merchant] += txn.amount

        return {
            "category": category or "all categories",
            "month": selection.period_label,
            "total": selection.total,
            "transaction_count": selection.count,
            "average_transaction": selection.average_transaction,
            "average_per_week": selection.average_per_week,
            # Spelled out so the model can relay *how* the average was framed.
            # "Per week" is ambiguous until you say over what window.
            "weeks_in_period": round(selection.period.weeks, 2),
            "period_start": selection.period.start.isoformat(),
            "period_end": selection.period.end.isoformat(),
            "top_merchants": [
                {"merchant": m, "total": round(v, 2)}
                for m, v in sorted(by_merchant.items(), key=lambda kv: -kv[1])[:5]
            ],
        }

    def top_categories(self, month: str | None = None, limit: int = 3) -> dict[str, Any]:
        selection = self._selector.select(month=month)

        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for txn in selection.transactions:
            totals[txn.category] += txn.amount
            counts[txn.category] += 1

        grand = sum(totals.values())
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])[: max(1, limit)]

        return {
            "month": selection.period_label,
            "total_spend": round(grand, 2),
            "category_count": len(totals),
            "categories": [
                {
                    "category": name,
                    "total": round(value, 2),
                    "transaction_count": counts[name],
                    "share_of_spend": round(100 * value / grand, 1) if grand else 0.0,
                }
                for name, value in ranked
            ],
        }
