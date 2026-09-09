"""The interfaces this application requires of the outside world.

Each port is the *narrowest* interface its consumers actually need — Interface
Segregation applied literally. `InsightService` needs to read transactions and
nothing else, so `TransactionRepository` has no `save`. The conversation layer
needs to append a turn and read history, so `SessionRepository` exposes exactly
those two operations and no session lifecycle.

These are `Protocol`s rather than abstract base classes on purpose: the
concrete adapters in `penny.infrastructure` never import this module, so there
is no inheritance coupling in either direction. An adapter satisfies a port by
having the right shape, which also means a test double is a plain class with
three methods, not a subclass.
"""

from penny.application.ports.agents import AgentRuntime, GreetingRuntime
from penny.application.ports.audit import AuditRecord, AuditSink
from penny.application.ports.clock import Clock
from penny.application.ports.sessions import Session, SessionRepository
from penny.application.ports.transactions import TransactionRepository

__all__ = [
    "AgentRuntime",
    "AuditRecord",
    "AuditSink",
    "Clock",
    "GreetingRuntime",
    "Session",
    "SessionRepository",
    "TransactionRepository",
]
