"""The regulated record of what the agent was asked and what it answered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AuditRecord:
    request_id: str
    session_id: str | None
    poid: str
    client_query: str
    ai_response: str
    tools_called: tuple[str, ...]
    model_id: str | None
    prompt_version: str


@runtime_checkable
class AuditSink(Protocol):
    """Append-only, fire-and-forget.

    `record` returns immediately and must never raise: in a bank the audit
    trail is mandatory, but a customer's answer must not be delayed by it and
    must certainly not fail because of it. Durability is the sink's problem to
    solve behind this method, not the caller's to wait on.
    """

    def record(self, entry: AuditRecord) -> None: ...
