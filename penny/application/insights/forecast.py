"""Month-end projection.

Answers "predict my likely end-of-month balance" and "am I on track".

Both of those questions ask for a *balance*, and this is the clearest example in
the assignment of a story that transaction history cannot fully answer. There is
no opening balance in the file, so no end-of-month balance is derivable at any
confidence. Projected month-end **spending** is derivable, and that is what this
returns — labelled as spending, with the limitation stated in the payload so the
model cannot quietly answer a different question than the one asked.
"""

from __future__ import annotations

import statistics
from typing import Any

from penny.application.insights.selection import TransactionSelector
from penny.application.ports.transactions import TransactionRepository
from penny.domain.periods import Period, days_in_month, is_month_fully_covered


class ForecastInsights:
    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository
        self._selector = TransactionSelector(repository)

    def month_end(self, month: str | None = None) -> dict[str, Any]:
        months = self._repository.months()
        first, latest = self._repository.date_bounds()
        month = month or (months[-1] if months else latest.strftime("%Y-%m"))

        selection = self._selector.select(month=month)
        if not selection.transactions:
            return {
                "month": month,
                "error": f"No transactions in {month}.",
                "available_months": months,
            }

        total_days = days_in_month(month)
        # Elapsed days come from the calendar clipped to the data, not from the
        # last transaction date: a quiet final week is real information about
        # the run rate, and using the last purchase as "today" would erase it.
        month_period = Period.for_month(month)
        effective_today = min(month_period.end, latest)
        days_elapsed = max(effective_today.day, 1)

        spend = selection.total
        daily_rate = spend / days_elapsed

        # A baseline is only meaningful against months the data covers in full.
        # The first month of a dataset is usually a stub — this sample opens on
        # 2026-01-28 — and averaging it in understates the norm badly enough to
        # invert the answer to "am I on track".
        prior_months = [m for m in months if m < month]
        baseline_months = [m for m in prior_months if is_month_fully_covered(m, first, latest)]
        partial_months = [m for m in prior_months if m not in baseline_months]
        prior_totals = [self._selector.spend_in_month(m) for m in baseline_months]
        prior_average = round(statistics.mean(prior_totals), 2) if prior_totals else None

        projected = round(daily_rate * total_days, 2)

        return {
            "month": month,
            "spend_so_far": spend,
            "days_elapsed": days_elapsed,
            "days_in_month": total_days,
            "daily_run_rate": round(daily_rate, 2),
            "projected_month_end_spend": projected,
            "prior_month_average_spend": prior_average,
            "prior_months_used": baseline_months,
            "prior_months_excluded_as_partial": partial_months,
            "versus_prior_average": (
                round(projected - prior_average, 2) if prior_average is not None else None
            ),
            "baseline_note": (
                f"Averaged over {len(baseline_months)} complete month(s): "
                f"{', '.join(baseline_months)}."
                + (
                    f" Excluded {', '.join(partial_months)} - the data does not cover "
                    "the whole month, so including it would understate the norm."
                    if partial_months
                    else ""
                )
                if baseline_months
                else (
                    "No prior month is covered in full, so there is no baseline to "
                    "compare against. Do not invent one; say the history is too short."
                )
            ),
            "method": (
                "Straight-line run rate: spend so far / days elapsed x days in the month. "
                "It assumes the rest of the month resembles the part observed, which is "
                "a weak assumption around rent, payday and holidays."
            ),
            "caveat": (
                "This projects SPENDING, not an account balance. This dataset contains no "
                "opening balance, so an end-of-month balance cannot be derived at all. If "
                "the customer asked for a balance, say that plainly before giving this."
            ),
        }
