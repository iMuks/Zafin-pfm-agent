"""Merchant-level questions, and the raw transaction list behind them."""

from __future__ import annotations

from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository

#: Hard ceiling on rows returned to the model. A tool result becomes context,
#: and the whole point of tool calling here is that the history never does.
MAX_TRANSACTIONS = 50


class MerchantInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._selector = TransactionSelector(repository)

    def top_merchants(
        self, month: str | None = None, category: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        selection = self._selector.select(month=month, category=category)

        aggregated: dict[str, dict[str, Any]] = {}
        for txn in selection.transactions:
            row = aggregated.setdefault(
                txn.merchant,
                {
                    "merchant": txn.merchant,
                    "total": 0.0,
                    "visits": 0,
                    "category": txn.category,
                    "logo_domain": txn.logo_domain,
                    "last_seen": txn.iso_date,
                },
            )
            row["total"] += txn.amount
            row["visits"] += 1
            row["last_seen"] = txn.iso_date  # repository order is ascending

        ranked = sorted(aggregated.values(), key=lambda r: -r["total"])[: max(1, limit)]
        for row in ranked:
            row["total"] = round(row["total"], 2)
            row["average_per_visit"] = round(row["total"] / row["visits"], 2)

        return {
            "month": selection.period_label,
            "category": category,
            "merchant_count": len(aggregated),
            "merchants": ranked,
        }

    def find_transactions(
        self,
        merchant: str | None = None,
        category: str | None = None,
        month: str | None = None,
        min_amount: float | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        selection = self._selector.select(
            month=month, category=category, merchant=merchant, min_amount=min_amount
        )
        # Newest first: "show me my recent X" wants the recent ones, and a
        # truncated list should keep the rows the customer is thinking about.
        ordered = sorted(selection.transactions, key=lambda t: t.day, reverse=True)
        shown = ordered[: min(max(1, limit), MAX_TRANSACTIONS)]

        return {
            "transactions": [t.to_public() for t in shown],
            "match_count": selection.count,
            "shown": len(shown),
            "total_of_matches": selection.total,
            "truncated": len(shown) < selection.count,
        }
