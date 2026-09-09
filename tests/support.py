"""Shared test helpers.

Everything here exists so an insight can be tested against a handful of
hand-written transactions — no JSON file, no fixture to keep in sync with the
real dataset, and no API key.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from langchain_core.language_models import GenericFakeChatModel

from penny.application.conversation.prompts.assembler import PromptAssembler
from penny.domain.models import Transaction
from penny.infrastructure.observability.context import RequestContext, set_context
from penny.infrastructure.observability.ids import new_id
from penny.infrastructure.persistence.json_transactions import InMemoryTransactionRepository

_COUNTER = {"n": 0}


def txn(
    amount: float,
    day: str,
    *,
    merchant: str = "Test Merchant",
    category: str = "Groceries",
    direction: str = "debit",
    is_recurring: bool = False,
    description: str | None = None,
    logo_domain: str | None = None,
    city: str | None = None,
    region: str | None = None,
) -> Transaction:
    _COUNTER["n"] += 1
    return Transaction(
        id=f"t{_COUNTER['n']:04d}",
        day=date.fromisoformat(day),
        description=description or merchant.upper(),
        amount=amount,
        merchant=merchant,
        category=category,
        direction=direction,  # type: ignore[arg-type]
        is_recurring=is_recurring,
        logo_domain=logo_domain,
        city=city,
        region=region,
    )


def repo(*transactions: Transaction) -> InMemoryTransactionRepository:
    return InMemoryTransactionRepository(transactions)


class FakeChatModel(GenericFakeChatModel):
    """A fake chat model `create_agent` will accept.

    `GenericFakeChatModel` does not implement `bind_tools`, so the agent factory
    rejects it outright. Binding is a deliberate no-op: these tests drive the
    streaming and component path, not the model's tool selection.
    """

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        return self


def request_context(poid: str = "test-customer") -> RequestContext:
    """Install a request context, as HTTP middleware would."""
    ctx = RequestContext(request_id=new_id("req"), poid=poid, model_id="fake-model")
    set_context(ctx)
    return ctx


def chat_runtime(model: Any, *, sessions: Any | None = None) -> Any:
    """A PennyChatRuntime wired entirely from in-memory collaborators."""
    from penny.application.insights.service import InsightService
    from penny.application.tools.catalog import build_catalog
    from penny.application.tools.registry import ToolRegistry
    from penny.infrastructure.llm.chat_runtime import PennyChatRuntime
    from penny.infrastructure.observability.audit_log import NullAuditSink
    from penny.infrastructure.persistence.memory_sessions import InMemorySessionRepository

    repository = repo(
        txn(120.00, "2026-05-04", merchant="Whole Foods"),
        txn(80.50, "2026-06-09", merchant="Whole Foods"),
        txn(22.99, "2026-06-11", merchant="Netflix", category="Subscriptions", is_recurring=True),
    )
    return PennyChatRuntime(
        registry=ToolRegistry(build_catalog(InsightService(repository))),
        prompts=PromptAssembler(repository),
        sessions=sessions or InMemorySessionRepository(1800),
        audit=NullAuditSink(),
        model=model,
    )
