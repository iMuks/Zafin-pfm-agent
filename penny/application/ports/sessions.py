"""Conversation memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Turn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    poid: str
    expires_at: float


@runtime_checkable
class SessionRepository(Protocol):
    """Server-side conversation history, scoped to one customer.

    History lives behind this port rather than in the request body, which is a
    security property and not a convenience: a client that cannot send history
    cannot rewrite it, so it cannot forge an earlier turn in which Penny
    "agreed" to something.
    """

    def get_or_create(self, poid: str, session_id: str | None) -> Session:
        """Return the live session for this customer, or start a new one.

        A session id belonging to a *different* customer must be treated as
        absent, never honoured.
        """
        ...

    def append_turn(self, session_id: str, role: str, content: str) -> None: ...

    def history(self, session_id: str, limit: int = 20) -> list[Turn]:
        """The most recent turns, oldest first. Empty for an unknown session."""
        ...
