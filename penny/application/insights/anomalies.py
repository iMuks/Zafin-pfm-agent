"""Anomaly detection — two separate signals, never conflated.

The assignment lists "duplicate charges" and "unusual transactions" as distinct
user stories, and they are genuinely different questions with different failure
modes. They are computed and reported separately so that Penny can tell the
customer *which* rule fired and why, instead of presenting one opaque list of
"suspicious" items.

Neither signal is a fraud verdict. `method` travels with the result and the
system prompt requires Penny to relay it, because "we flagged this" without
"here is the rule" is how an assistant frightens someone about a legitimate
holiday purchase.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository
from penny.domain.models import Transaction

#: Same merchant, same amount, this close together — a double post.
DUPLICATE_WINDOW_DAYS = 3
#: Standard deviations above a category's own mean before a charge is unusual.
OUTLIER_SIGMA = 2.0
#: Below this, a category's mean and deviation are not worth trusting.
MIN_SAMPLE_FOR_OUTLIERS = 5
MAX_OUTLIERS_REPORTED = 10


class AnomalyInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._selector = TransactionSelector(repository)

    def detect(self, month: str | None = None) -> dict[str, Any]:
        selection = self._selector.select(month=month)
        transactions = selection.transactions

        return {
            "month": selection.period_label,
            "duplicate_charges": self._duplicates(transactions),
            "unusual_transactions": self._outliers(transactions),
            "method": (
                f"Duplicates: the same merchant charging the same amount within "
                f"{DUPLICATE_WINDOW_DAYS} days. Outliers: a charge more than "
                f"{OUTLIER_SIGMA:g} standard deviations above the mean for its OWN category, "
                f"so a large grocery run is judged against groceries rather than against "
                f"a flight. Categories with fewer than {MIN_SAMPLE_FOR_OUTLIERS} transactions "
                f"are skipped as too small to have a meaningful average."
            ),
            "caveat": (
                "These are statistical flags, not fraud findings. An unusual charge is "
                "very often a legitimate one-off. Present them as 'worth a look'."
            ),
        }

    @staticmethod
    def _duplicates(transactions: tuple[Transaction, ...]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, float], list[Transaction]] = defaultdict(list)
        for txn in transactions:
            grouped[(txn.merchant, txn.amount)].append(txn)

        found: list[dict[str, Any]] = []
        for (merchant, amount), group in grouped.items():
            # Adjacent pairs only: three charges in a row are two overlapping
            # duplicate events, which is the honest description of what posted.
            for earlier, later in zip(group, group[1:], strict=False):
                gap = (later.day - earlier.day).days
                if gap <= DUPLICATE_WINDOW_DAYS:
                    found.append(
                        {
                            "merchant": merchant,
                            "amount": amount,
                            "dates": [earlier.iso_date, later.iso_date],
                            "days_apart": gap,
                            "transaction_ids": [earlier.id, later.id],
                        }
                    )
        found.sort(key=lambda d: -d["amount"])
        return found

    @staticmethod
    def _outliers(transactions: tuple[Transaction, ...]) -> list[dict[str, Any]]:
        by_category: dict[str, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            by_category[txn.category].append(txn)

        found: list[dict[str, Any]] = []
        for group in by_category.values():
            amounts = [t.amount for t in group]
            if len(amounts) < MIN_SAMPLE_FOR_OUTLIERS:
                continue
            mean = statistics.mean(amounts)
            deviation = statistics.pstdev(amounts)
            if deviation == 0 or mean == 0:
                continue
            threshold = mean + OUTLIER_SIGMA * deviation
            for txn in group:
                if txn.amount > threshold:
                    found.append(
                        {
                            **txn.to_public(),
                            "category_average": round(mean, 2),
                            "times_category_average": round(txn.amount / mean, 1),
                        }
                    )

        found.sort(key=lambda o: -o["amount"])
        return found[:MAX_OUTLIERS_REPORTED]
