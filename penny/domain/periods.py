"""Calendar-month arithmetic, as a value object.

Every insight is scoped to a period, and "how many weeks is this?" turns out to
be the question that decides whether an average is right or subtly wrong. The
naive answer — span the first and last matching transaction — silently changes
the denominator with the data: a customer who bought groceries twice in one
week of July gets a "weekly average" computed over one week, which is not a
weekly average at all.

`Period` fixes the denominator to the *calendar* window being asked about,
independently of which days happen to contain transactions.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Period:
    """A closed date interval, inclusive at both ends."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Period end {self.end} precedes start {self.start}.")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def weeks(self) -> float:
        """Fractional weeks, floored at 1 so a short period cannot inflate an average."""
        return max(self.days / 7, 1.0)

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @classmethod
    def for_month(cls, month: str) -> Period:
        """The full calendar month named by "YYYY-MM", whatever the data holds."""
        year, mon = parse_month(month)
        return cls(date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1]))

    @classmethod
    def spanning(cls, first: date, last: date) -> Period:
        return cls(first, last)


def parse_month(month: str) -> tuple[int, int]:
    """Parse "YYYY-MM" into (year, month), raising ValueError on anything else."""
    try:
        year, mon = month.split("-")
        year_i, mon_i = int(year), int(mon)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f'Month must look like "YYYY-MM", got {month!r}.') from exc
    if not 1 <= mon_i <= 12:
        raise ValueError(f"Month {mon_i} is out of range in {month!r}.")
    return year_i, mon_i


def days_in_month(month: str) -> int:
    year, mon = parse_month(month)
    return calendar.monthrange(year, mon)[1]


def previous_month(month: str) -> str:
    year, mon = parse_month(month)
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def is_month_complete(month: str, latest: date) -> bool:
    """True when the dataset reaches the final calendar day of `month`.

    The sample history stops on 2026-07-28, three days short of month end.
    Summarising that as "July" understates the total, and comparing it to a
    full June is simply wrong. Completeness is therefore a first-class fact
    that the prompt, the greeting and the comparison tools all consult.
    """
    return latest >= Period.for_month(month).end


def is_month_fully_covered(month: str, first: date, latest: date) -> bool:
    """True when the data spans every calendar day of `month`, at *both* ends.

    `is_month_complete` asks only whether the data reaches the end of a month.
    That is the right question for the trailing month and the wrong one for the
    leading month: this sample starts 2026-01-28, so January holds four days —
    yet it reaches its own month end and passes that check. Averaging a stub in
    as though it were a whole month drags a baseline down; on this dataset it is
    enough to flip a month-on-month verdict from "under" to "over".
    """
    period = Period.for_month(month)
    return first <= period.start and latest >= period.end


def last_complete_month(months: list[str], latest: date) -> str:
    """The most recent month the data covers to its final day.

    Falls back to the newest month when the dataset holds only a partial one —
    a single incomplete month is still better than reporting nothing.
    """
    if not months:
        return ""
    if is_month_complete(months[-1], latest):
        return months[-1]
    return months[-2] if len(months) > 1 else months[-1]
