"""The composition root — the only module allowed to name a concrete class.

Every other module in this codebase depends on a Protocol. This one wires the
implementations together, which is what makes "swap the session store for
DynamoDB" a change to one file.

Construction is lazy for a specific reason: building the agent creates a model
client, and importing the app must never require an API key. A reviewer with no
credentials can still import the package, run the test suite and open the
component gallery — the credential check happens when a *live* runtime is first
requested, and nowhere else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from penny.application.conversation.greeting import GreetingFacts
from penny.application.conversation.prompts.assembler import PromptAssembler
from penny.application.insights.service import InsightService
from penny.application.ports.agents import AgentRuntime, GreetingRuntime
from penny.application.ports.audit import AuditSink
from penny.application.ports.sessions import SessionRepository
from penny.application.ports.transactions import TransactionRepository
from penny.application.tools.catalog import build_catalog
from penny.application.tools.registry import ToolRegistry
from penny.domain.errors import MissingCredentialsError
from penny.infrastructure.config.models import Purpose, resolve_model
from penny.infrastructure.config.settings import settings
from penny.infrastructure.observability.audit_log import FileAuditSink
from penny.infrastructure.persistence.json_transactions import JsonTransactionRepository
from penny.infrastructure.persistence.memory_sessions import InMemorySessionRepository

#: Repository root — the directory holding `data/`, `web/` and `penny/`.
ROOT = Path(__file__).resolve().parents[2]


class Container:
    """Lazily-constructed singletons, resolved by Protocol."""

    def __init__(self) -> None:
        self._transactions: TransactionRepository | None = None
        self._insights: InsightService | None = None
        self._registry: ToolRegistry | None = None
        self._prompts: PromptAssembler | None = None
        self._sessions: SessionRepository | None = None
        self._audit: AuditSink | None = None
        self._chat: AgentRuntime | None = None
        self._greeting: GreetingRuntime | None = None

    # -- data -------------------------------------------------------------

    def transactions(self) -> TransactionRepository:
        if self._transactions is None:
            self._transactions = JsonTransactionRepository(ROOT / settings().enriched_path)
        return self._transactions

    def insights(self) -> InsightService:
        if self._insights is None:
            self._insights = InsightService(self.transactions())
        return self._insights

    def tool_registry(self) -> ToolRegistry:
        if self._registry is None:
            self._registry = ToolRegistry(build_catalog(self.insights()))
        return self._registry

    def prompts(self) -> PromptAssembler:
        if self._prompts is None:
            self._prompts = PromptAssembler(self.transactions(), settings().prompt_version)
        return self._prompts

    # -- infrastructure ---------------------------------------------------

    def sessions(self) -> SessionRepository:
        if self._sessions is None:
            self._sessions = InMemorySessionRepository(settings().session_ttl_seconds)
        return self._sessions

    def audit(self) -> AuditSink:
        if self._audit is None:
            cfg = settings()
            self._audit = FileAuditSink(ROOT / cfg.audit_log_path, enabled=cfg.enable_audit_log)
        return self._audit

    # -- runtimes ---------------------------------------------------------

    @staticmethod
    def _require_credentials(purpose: Purpose) -> None:
        """Checked at construction, so request handlers stay unaware of it.

        An injected stub never reaches this path, which is exactly why the
        component gallery runs with no API key at all. A local-provider model
        needs no key: its endpoint is inside the deployment, and the enclave
        guard in the model factory is what checks it.
        """
        if resolve_model(purpose).provider == "local":
            return
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise MissingCredentialsError

    def chat_runtime(self) -> AgentRuntime:
        if self._chat is None:
            self._require_credentials("chat")
            # Imported here rather than at module scope: LangGraph is a heavy
            # import, and nothing that only reads data should pay for it.
            from penny.infrastructure.llm.chat_runtime import PennyChatRuntime

            self._chat = PennyChatRuntime(
                registry=self.tool_registry(),
                prompts=self.prompts(),
                sessions=self.sessions(),
                audit=self.audit(),
            )
        return self._chat

    def greeting_runtime(self) -> GreetingRuntime:
        if self._greeting is None:
            self._require_credentials("greeting")
            from penny.infrastructure.llm.greeting_runtime import PennyGreetingRuntime

            self._greeting = PennyGreetingRuntime(
                facts=GreetingFacts(self.transactions(), self.insights()),
                prompts=self.prompts(),
            )
        return self._greeting

    # -- test / gallery support ------------------------------------------

    def override(self, **replacements: Any) -> None:
        """Inject stand-ins. Used by the component gallery and by tests."""
        mapping = {
            "transactions": "_transactions",
            "insights": "_insights",
            "registry": "_registry",
            "prompts": "_prompts",
            "sessions": "_sessions",
            "audit": "_audit",
            "chat": "_chat",
            "greeting": "_greeting",
        }
        for key, value in replacements.items():
            attribute = mapping.get(key)
            if attribute is None:
                raise KeyError(f"Unknown collaborator {key!r}. Known: {sorted(mapping)}.")
            setattr(self, attribute, value)


#: The process-wide container. Request handlers ask this for collaborators.
container = Container()


def override(**replacements: Any) -> None:
    container.override(**replacements)
