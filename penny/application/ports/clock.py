"""Time as a dependency.

Session expiry and "how far into the month are we" are both time-dependent, and
both need to be testable without sleeping. Injecting the clock is what lets a
test assert that a session expires without waiting thirty minutes for it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Seconds since the epoch."""
        ...
