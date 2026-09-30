"""Domain events: what happened, said once, for anyone who cares.

Penny is event-driven from the source to the screen. A bank posts a
transaction, the aggregator tells us, that is an event; the ledger commits a
new snapshot, that is an event; a source loses its credential, that is an
event. Handlers react; nothing runs on a timer. Events are immutable facts
with an id, so a handler that sees one twice can ignore the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base fact. `name` is the routing key handlers subscribe to."""

    tenant_id: str
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=_now)

    @property
    def name(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshRequested(Event):
    """Someone wants fresh data now: the customer opened the app or pulled to
    refresh, or a re-authorization just completed. `source_id` None means every
    linked source of the tenant."""

    source_id: str | None
    reason: str  # app_open | pull_to_refresh | reauth_completed


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionsAvailable(Event):
    """The source (aggregator webhook, bank API, file upload) has new or
    changed records for one source. Carries a hint, never the records."""

    source_id: str
    hint: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotCommitted(Event):
    """The ledger has a new tenant-global version. Clients refresh on this."""

    version: int
    source_id: str
    coverage_through: date | None
    rows_in: int
    rows_retired: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceStateChanged(Event):
    """A source moved between linked, syncing, needs_reauth, blocked, disconnected."""

    source_id: str
    state: str
    cause: str | None = None
