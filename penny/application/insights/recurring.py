"""Recurring charges — "show me all my subscriptions".

Two independent signals must agree before a merchant is called a subscription:

1. **Nature** — enrichment flagged the merchant as one a customer subscribes to
   (streaming, SaaS, telecom, insurance). This is a judgement about the brand.
2. **Behaviour** — it has actually charged more than once, at a readable cadence.
   This is a fact about the account.

Requiring both is what stops a single Netflix gift-card purchase being reported
as a monthly subscription, and stops a weekly grocery run being reported as one
just because it repeats.

A note on this dataset: `sample_transactions.csv` is synthetic, and its amounts
are randomised — Netflix appears between $6.59 and $52.45, and Hulu posts on
three consecutive days in July. Real subscription data is near-constant in both
amount and cadence. The detector is therefore deliberately explicit about what
it observed (`amount_varies`, `cadence_confidence`) rather than asserting a
tidy monthly figure the data does not support.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from penny.application.ports.transactions import TransactionRepository
from penny.domain.models import Transaction

#: Median gap, in days, mapped to a human cadence. Upper bounds are generous
#: because billing dates drift (28th vs 1st) and a strict 30 would misclassify.
_CADENCE_BANDS: tuple[tuple[int, str], ...] = (
    (10, "weekly"),
    (45, "monthly"),
    (120, "quarterly"),
    (400, "annual"),
)

MIN_CHARGES = 2


def _cadence(median_gap: float) -> str:
    for upper, label in _CADENCE_BANDS:
        if median_gap <= upper:
            return label
    return "irregular"


class RecurringInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository

    def subscriptions(self) -> dict[str, Any]:
        by_merchant: dict[str, list[Transaction]] = defaultdict(list)
        for txn in self._repository.all():
            if txn.is_spend and txn.is_recurring:
                by_merchant[txn.merchant].append(txn)

        found: list[dict[str, Any]] = []
        for merchant, charges in by_merchant.items():
            if len(charges) < MIN_CHARGES:
                continue

            gaps = [(b.day - a.day).days for a, b in zip(charges, charges[1:], strict=False)]
            median_gap = statistics.median(gaps)
            amounts = [t.amount for t in charges]
            typical = round(statistics.median(amounts), 2)
            spread = round(max(amounts) - min(amounts), 2)

            found.append(
                {
                    "merchant": merchant,
                    "category": charges[0].category,
                    "logo_domain": charges[0].logo_domain,
                    "charge_count": len(charges),
                    "cadence": _cadence(median_gap),
                    "median_days_between_charges": round(median_gap, 1),
                    "typical_amount": typical,
                    "total_paid": round(sum(amounts), 2),
                    "first_seen": charges[0].iso_date,
                    "last_seen": charges[-1].iso_date,
                    # A real subscription barely moves. A large spread means the
                    # "typical amount" is a median over noise, and saying so is
                    # more useful than quoting it as if it were a price.
                    "amount_varies": spread > 1.0,
                    "amount_spread": spread,
                    "cadence_confidence": "low" if _irregular(gaps) else "high",
                }
            )

        found.sort(key=lambda s: -s["total_paid"])
        monthly = sum(s["typical_amount"] for s in found if s["cadence"] == "monthly")

        return {
            "subscriptions": found,
            "count": len(found),
            "estimated_monthly_cost": round(monthly, 2),
            "method": (
                "A merchant is reported as a subscription only if enrichment flagged it "
                f"as subscription-like AND it charged at least {MIN_CHARGES} times. Cadence is "
                "the median gap between charges. Bank fees recur but are not subscriptions, "
                "so they are excluded by design."
            ),
            "caveat": (
                "Where amount_varies is true the charge amount is not stable, so "
                "typical_amount is a median rather than a price. Report it as such."
            ),
        }


def _irregular(gaps: list[int]) -> bool:
    """True when the gaps are too scattered for the cadence label to mean much."""
    if len(gaps) < 2:
        return True
    median = statistics.median(gaps)
    if median <= 0:
        return True
    return statistics.pstdev(gaps) / median > 0.5
