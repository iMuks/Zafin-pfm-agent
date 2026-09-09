"""Income, spending and the ratio between them.

Answers "how much of my income goes to food and dining" and the savings-rate
family.

This is the insight most at risk of being confidently wrong, and the reason is
in the data rather than in the code. A transaction file shows only what moved
through *this* account. In `sample_transactions.csv` that is two payroll credits
totalling $4,379 across six months, against tens of thousands in debits — which
would imply a savings rate of roughly minus a thousand percent. The customer is
not insolvent; their salary is simply landing somewhere this file cannot see.

So the caveat is returned as part of the payload, not left in the prompt, and
`income_looks_complete` is computed rather than assumed. A tool that can be
wrong should say when it probably is.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository

#: Below this many income credits per month of history, treat the income
#: picture as partial. A salaried US customer paid into this account would
#: show at least monthly (usually twice-monthly) credits.
MIN_CREDITS_PER_MONTH = 1.0


class IncomeInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository
        self._selector = TransactionSelector(repository)

    def income_and_savings(self, month: str | None = None) -> dict[str, Any]:
        all_txns = self._repository.all()
        scope = [t for t in all_txns if month is None or t.month == month]

        income_txns = [t for t in scope if t.is_income]
        income = round(sum(t.amount for t in income_txns), 2)

        spend_selection = self._selector.select(month=month)
        spending = spend_selection.total

        by_category: dict[str, float] = defaultdict(float)
        for txn in spend_selection.transactions:
            by_category[txn.category] += txn.amount

        months_covered = 1 if month else max(len(self._repository.months()), 1)
        looks_complete = len(income_txns) >= MIN_CREDITS_PER_MONTH * months_covered

        return {
            "month": spend_selection.period_label,
            "income": income,
            "income_transaction_count": len(income_txns),
            "spending": spending,
            "net": round(income - spending, 2),
            "savings_rate_percent": round(100 * (income - spending) / income, 1)
            if income
            else None,
            "spend_share_of_income": (
                {
                    name: round(100 * value / income, 1)
                    for name, value in sorted(by_category.items(), key=lambda kv: -kv[1])
                }
                if income
                else {}
            ),
            "income_looks_complete": looks_complete,
            "caveat": (
                "Income here is only what appears as Income-category credits in this "
                "transaction file. "
                + (
                    ""
                    if looks_complete
                    else f"Only {len(income_txns)} income credit(s) appear across "
                    f"{months_covered} month(s) of history, which is far too few for a "
                    "salaried customer. Salary is almost certainly paid into another "
                    "account. "
                )
                + "Do NOT present these ratios as a true savings rate — describe them as "
                "'of the income visible in this account' and say the picture is partial."
            ),
        }
