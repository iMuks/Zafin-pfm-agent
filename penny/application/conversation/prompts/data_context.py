"""Part 3 — the pre-fetched data context.

Computed from the repository rather than typed in, so it cannot drift from the
dataset it describes. This is where the agent is told which months exist and,
critically, that "today" is the last date in the data.

Without that anchor the sample history — which ends 2026-07-28 — makes every
"this month" question return an empty result set against the real wall clock,
which reads as a broken agent rather than as a dataset with an end date.
"""

from __future__ import annotations

from penny.application.ports.transactions import TransactionRepository
from penny.domain.periods import is_month_complete, last_complete_month
from penny.domain.taxonomy import CATEGORIES


def build(repository: TransactionRepository) -> str:
    _, latest = repository.date_bounds()
    first, _ = repository.date_bounds()
    months = repository.months()
    if not months:
        return "# Data context\nThe transaction history is empty."

    current = months[-1]
    previous = months[-2] if len(months) > 1 else current
    complete = last_complete_month(months, latest)

    lines = [
        "# Data context",
        f"Treat {latest.isoformat()} as today. The customer's transaction history runs "
        f"from {first.isoformat()} to {latest.isoformat()} "
        f"({len(months)} months: {', '.join(months)}).",
        f'"This month" means {current}. "Last month" means {previous}.',
        f"The most recent COMPLETE month is {complete}. Prefer it for any summary or "
        f"month-over-month comparison.",
    ]

    if not is_month_complete(current, latest):
        lines.append(
            f"{current} is PARTIAL — the data stops on day {latest.day}. If you report it, "
            f'say "so far", and never compare it against a full month without saying that '
            f"the comparison is not like-for-like."
        )

    lines.append(f"Spending categories in this dataset: {', '.join(CATEGORIES)}.")
    return "\n".join(lines)
