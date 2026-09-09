"""The category vocabulary — a closed list, deliberately.

Zafin's production Transaction Enrichment resolves 70+ categories. A prototype
does not need that breadth, but it does need the same property: a *closed* set.
An open-ended "pick a category" prompt returns "Dining", "Restaurants",
"Food & Drink" and "Eating Out" across different batches, and every downstream
`GROUP BY category` silently splits into near-duplicates. Constraining the
enrichment schema to this list is what makes the aggregates Penny reports
trustworthy.

The list is sized to the dataset it describes: every bucket below is populated
by `data/sample_transactions.csv`, except `Education`, which is kept because a
US retail deposit account normally has one.
"""

from __future__ import annotations

from typing import Final

CATEGORIES: Final[tuple[str, ...]] = (
    "Dining",
    "Groceries",
    "Subscriptions",
    "Transport",
    "Fuel",
    "Travel",
    "Shopping",
    "Health & Pharmacy",
    "Entertainment",
    "Utilities & Telecom",
    "Insurance",
    "Fees & Charges",
    "Taxes & Government",
    "Charity & Donations",
    "Transfers",
    "Income",
    "Home",
    "Education",
    "Other",
)

#: Money movement and inbound pay — not consumption. Excluded from every
#: "what did I spend" total, so a $3,051 Venmo payment cannot dwarf a month of
#: real spending and a payroll credit is never counted as an expense.
NON_SPEND_CATEGORIES: Final[frozenset[str]] = frozenset({"Transfers", "Income"})

#: The single category that represents money arriving.
INCOME_CATEGORY: Final[str] = "Income"

_LOOKUP: Final[dict[str, str]] = {c.casefold(): c for c in CATEGORIES}


def is_valid(category: str) -> bool:
    return category.casefold() in _LOOKUP


def canonical(category: str) -> str | None:
    """Resolve a case-insensitive category name to its canonical spelling.

    Returns None for anything outside the closed list. Callers decide whether
    that is a validation error (enrichment) or a no-match filter (analytics);
    this function does not choose for them.
    """
    return _LOOKUP.get(category.strip().casefold())
