"""Time-sortable identifiers."""

from __future__ import annotations

import secrets
import time


def new_id(prefix: str, at: float | None = None) -> str:
    """`prefix_<hex milliseconds><random>`.

    The property that matters is the one UUID v7 provides: lexicographic order
    equals chronological order. "The customer's most recent session" becomes a
    sorted-key prefix scan rather than a table scan, which is what makes the
    DynamoDB shape in `memory_sessions` viable at scale.

    `at` lets a caller supply the timestamp from its own clock. Without it a
    component that takes an injected `Clock` for expiry would still mint ids off
    the wall clock, and the ordering guarantee would quietly not hold under the
    clock that component actually uses.
    """
    stamp = int((time.time() if at is None else at) * 1000)
    return f"{prefix}_{stamp:012x}{secrets.token_hex(4)}"
