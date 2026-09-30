"""The in-process event bus: ordered delivery, isolation, at-most-once."""

from __future__ import annotations

import unittest

from penny.domain.events import RefreshRequested, SnapshotCommitted
from penny.infrastructure.events.memory import InMemoryEventBus


class Bus(unittest.TestCase):
    def test_subscribers_receive_only_their_type_in_order(self):
        bus = InMemoryEventBus()
        seen: list[str] = []
        bus.subscribe(RefreshRequested, lambda e: seen.append("refresh"))
        bus.subscribe(SnapshotCommitted, lambda e: seen.append(f"snapshot:{e.version}"))
        bus.publish(RefreshRequested(tenant_id="t", source_id=None, reason="app_open"))
        bus.publish(
            SnapshotCommitted(
                tenant_id="t",
                version=3,
                source_id="s",
                coverage_through=None,
                rows_in=1,
                rows_retired=0,
            )
        )
        self.assertEqual(seen, ["refresh", "snapshot:3"])

    def test_a_failing_handler_does_not_starve_the_next(self):
        bus = InMemoryEventBus()
        seen: list[str] = []

        def boom(event):
            raise RuntimeError("handler bug")

        bus.subscribe(RefreshRequested, boom)
        bus.subscribe(RefreshRequested, lambda e: seen.append("ok"))
        bus.publish(RefreshRequested(tenant_id="t", source_id=None, reason="app_open"))
        self.assertEqual(seen, ["ok"])
        self.assertEqual(len(bus.published), 1)

    def test_a_republished_fact_is_delivered_once(self):
        bus = InMemoryEventBus()
        seen: list[str] = []
        bus.subscribe(RefreshRequested, lambda e: seen.append(e.event_id))
        event = RefreshRequested(tenant_id="t", source_id="s", reason="pull_to_refresh")
        bus.publish(event)
        bus.publish(event)
        self.assertEqual(seen, [event.event_id])
