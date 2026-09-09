"""In-process conversation memory with a fixed TTL.

A production deployment would put this in DynamoDB single-table form —
`PK=USER#{poid}`, `SK=SESSION#{id} | #TURN#{ts}` — and the shape here is
deliberately the same one, so that migration is a new class behind the same
port rather than a redesign:

* **Time-sortable session ids**, so "the customer's latest session" is a sort
  rather than a scan.
* **One record per turn**, which upstream is what avoids the 400 KB item ceiling
  on a long conversation.
* **TTL fixed at creation and never refreshed**, so a session cannot be kept
  alive indefinitely by continuing to chat. A banking session should expire on a
  wall clock, not on activity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from penny.application.ports.clock import Clock
from penny.application.ports.sessions import Session, SessionRepository, Turn
from penny.infrastructure.observability.ids import new_id


class SystemClock(Clock):
    def now(self) -> float:
        return time.time()


@dataclass
class _StoredSession:
    session_id: str
    poid: str
    created_at: float
    expires_at: float
    turns: list[Turn] = field(default_factory=list)


class InMemorySessionRepository(SessionRepository):
    def __init__(self, ttl_seconds: int, clock: Clock | None = None) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or SystemClock()
        self._sessions: dict[str, _StoredSession] = {}

    def _expired(self, session: _StoredSession) -> bool:
        return self._clock.now() >= session.expires_at

    def _sweep(self) -> None:
        for sid in [s for s, sess in self._sessions.items() if self._expired(sess)]:
            del self._sessions[sid]

    def get_or_create(self, poid: str, session_id: str | None) -> Session:
        self._sweep()

        if session_id:
            existing = self._sessions.get(session_id)
            # A session belongs to exactly one customer. A valid id presented by
            # a different poid is treated as absent rather than honoured — that
            # is the difference between a session id and a bearer token.
            if existing and not self._expired(existing) and existing.poid == poid:
                return Session(existing.session_id, existing.poid, existing.expires_at)

        now = self._clock.now()
        stored = _StoredSession(
            # Minted from the injected clock, so ordering holds under whatever
            # notion of time this repository was constructed with.
            session_id=new_id("sess", at=now),
            poid=poid,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._sessions[stored.session_id] = stored
        return Session(stored.session_id, stored.poid, stored.expires_at)

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        session = self._sessions.get(session_id)
        if session and not self._expired(session):
            session.turns.append(Turn(role=role, content=content))

    def history(self, session_id: str, limit: int = 20) -> list[Turn]:
        session = self._sessions.get(session_id)
        if not session or self._expired(session):
            return []
        return list(session.turns[-limit:])
