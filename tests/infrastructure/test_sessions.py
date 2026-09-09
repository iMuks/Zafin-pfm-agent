"""Session lifecycle: TTL, ownership, and history ordering."""

from __future__ import annotations

import unittest

from penny.application.ports.clock import Clock
from penny.infrastructure.persistence.memory_sessions import InMemorySessionRepository


class FakeClock(Clock):
    """Time as a dependency — a 30-minute TTL is testable in microseconds."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Sessions(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.repo = InMemorySessionRepository(ttl_seconds=1800, clock=self.clock)

    def test_same_id_returns_the_same_session(self):
        first = self.repo.get_or_create("cust-1", None)
        again = self.repo.get_or_create("cust-1", first.session_id)
        self.assertEqual(first.session_id, again.session_id)

    def test_another_customers_session_id_is_not_honoured(self):
        """A session id is not a bearer token: it is scoped to its owner."""
        stolen = self.repo.get_or_create("cust-1", None)
        theirs = self.repo.get_or_create("cust-2", stolen.session_id)
        self.assertNotEqual(theirs.session_id, stolen.session_id)

    def test_history_is_isolated_between_sessions(self):
        a = self.repo.get_or_create("cust-1", None)
        b = self.repo.get_or_create("cust-2", None)
        self.repo.append_turn(a.session_id, "user", "mine")
        self.assertEqual(len(self.repo.history(a.session_id)), 1)
        self.assertEqual(self.repo.history(b.session_id), [])

    def test_history_is_oldest_first_and_capped(self):
        session = self.repo.get_or_create("cust-1", None)
        for i in range(10):
            self.repo.append_turn(session.session_id, "user", f"q{i}")
        recent = self.repo.history(session.session_id, limit=3)
        self.assertEqual([t.content for t in recent], ["q7", "q8", "q9"])

    def test_session_expires_and_is_not_refreshed_by_activity(self):
        """A banking session expires on a wall clock, not on activity."""
        session = self.repo.get_or_create("cust-1", None)
        self.clock.advance(1700)
        self.repo.append_turn(session.session_id, "user", "still here")
        self.clock.advance(200)  # now past the fixed 1800s TTL
        self.assertEqual(self.repo.history(session.session_id), [])
        fresh = self.repo.get_or_create("cust-1", session.session_id)
        self.assertNotEqual(fresh.session_id, session.session_id)

    def test_session_ids_sort_chronologically(self):
        first = self.repo.get_or_create("cust-1", None)
        self.clock.advance(5)
        second = self.repo.get_or_create("cust-1", None)
        self.assertLess(first.session_id, second.session_id)

    def test_unknown_session_history_is_empty_not_an_error(self):
        self.assertEqual(self.repo.history("sess_does_not_exist"), [])


if __name__ == "__main__":
    unittest.main()
