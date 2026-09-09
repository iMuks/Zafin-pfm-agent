"""Assembles the system prompt and stamps its version.

Returned as Anthropic content blocks with an explicit cache breakpoint after the
last stable part. The behavioral and format text — the large majority of the
prompt — is then read from cache on every turn after the first, which is the
single cheapest latency win available in a multi-turn agent.

Versioning matters for a different reason: the active version is written into
every audit record, so a change in Penny's behaviour six months from now is
attributable to a specific prompt revision rather than to "the model drifted".
"""

from __future__ import annotations

from typing import Any

from penny.application.conversation.prompts import data_context
from penny.application.conversation.prompts.behavioral import BEHAVIORAL
from penny.application.conversation.prompts.format_prompt import FORMAT
from penny.application.ports.transactions import TransactionRepository


class PromptAssembler:
    def __init__(self, repository: TransactionRepository, version: str = "v1.0") -> None:
        self._repository = repository
        self._version = version
        self._cached_blocks: tuple[dict[str, Any], ...] | None = None

    @property
    def version(self) -> str:
        return self._version

    def blocks(self, behavioral: str = BEHAVIORAL, fmt: str = FORMAT) -> list[dict[str, Any]]:
        """System-prompt content blocks, stable prefix first.

        Memoised because the data context is computed from the whole dataset and
        the result is identical for the lifetime of the process. Recomputing it
        per turn would be pure waste on the hot path.
        """
        if self._cached_blocks is None or behavioral is not BEHAVIORAL or fmt is not FORMAT:
            stable = f"{behavioral}\n\n{fmt}"
            blocks = (
                # The cache breakpoint sits at the end of the stable text.
                {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": data_context.build(self._repository)},
            )
            if behavioral is BEHAVIORAL and fmt is FORMAT:
                self._cached_blocks = blocks
            return list(blocks)
        return list(self._cached_blocks)
