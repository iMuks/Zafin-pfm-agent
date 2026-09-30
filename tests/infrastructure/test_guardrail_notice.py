"""Bedrock Guardrails intervention becomes a `notice`, never Penny's own words.

Bedrock reports an intervention as `stopReason: guardrail_intervened` and
substitutes canned text for the answer. The stream must drop that text, end
with the server's fixed notice, and audit the event with the request id only.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from penny.application.components.presenters import GUARDRAIL_NOTICE
from penny.application.conversation.prompts.assembler import PromptAssembler
from penny.application.insights.service import InsightService
from penny.application.ports.audit import AuditRecord
from penny.application.tools.catalog import build_catalog
from penny.application.tools.registry import ToolRegistry
from penny.infrastructure.llm.chat_runtime import PennyChatRuntime
from penny.infrastructure.llm.streaming.handler import components_from_events, guardrail_assessment
from penny.infrastructure.observability.telemetry import StreamTelemetry
from penny.infrastructure.persistence.memory_sessions import InMemorySessionRepository
from tests.support import FakeChatModel, repo, request_context, txn

BLOCKED_METADATA = {"stopReason": "guardrail_intervened", "RequestId": "req-bedrock-42"}


class BlockedFakeChatModel(FakeChatModel):
    """Streams one chunk the way Bedrock does when Guardrails intervenes."""

    def _stream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        chunk = AIMessageChunk(
            content="Sorry, the model cannot answer this question.",
            response_metadata=dict(BLOCKED_METADATA),
        )
        if run_manager:
            run_manager.on_llm_new_token(chunk.content, chunk=ChatGenerationChunk(message=chunk))
        yield ChatGenerationChunk(message=chunk)


class RecordingAudit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, entry: AuditRecord) -> None:
        self.records.append(entry)


class Detection(unittest.TestCase):
    def test_intervention_is_read_from_the_chunk_metadata(self):
        chunk = AIMessageChunk(content="x", response_metadata=dict(BLOCKED_METADATA))
        self.assertEqual(guardrail_assessment(chunk), "req-bedrock-42")

    def test_a_normal_chunk_is_not_an_intervention(self):
        chunk = AIMessageChunk(content="x", response_metadata={"stopReason": "end_turn"})
        self.assertIsNone(guardrail_assessment(chunk))


class Handler(unittest.IsolatedAsyncioTestCase):
    async def test_the_substituted_text_never_reaches_the_client(self):
        async def events():
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": AIMessageChunk(
                        content='{"component":"chat-response","body":"partial"}'
                    )
                },
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": AIMessageChunk(
                        content="canned refusal", response_metadata=dict(BLOCKED_METADATA)
                    )
                },
            }

        telemetry = StreamTelemetry()
        emitted = [c async for c in components_from_events(events(), telemetry)]
        self.assertEqual(emitted, [{"component": "chat-response", "body": "partial"}])
        self.assertTrue(telemetry.guardrail_intervened)
        self.assertEqual(telemetry.guardrail_assessment, "req-bedrock-42")


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def test_the_turn_ends_with_a_notice_and_is_audited_without_content(self):
        repository = repo(txn(80.50, "2026-06-09", merchant="Whole Foods"))
        sessions = InMemorySessionRepository(1800)
        audit = RecordingAudit()
        ctx = request_context()
        session = sessions.get_or_create(ctx.poid, None)
        runtime = PennyChatRuntime(
            registry=ToolRegistry(build_catalog(InsightService(repository))),
            prompts=PromptAssembler(repository),
            sessions=sessions,
            audit=audit,
            model=BlockedFakeChatModel(messages=iter([])),
        )
        emitted = [
            c
            async for c in runtime.stream(
                message="tell me my neighbour's balance", session_id=session.session_id
            )
        ]
        names = [c["component"] for c in emitted]
        self.assertEqual(names[-2:], ["notice", "done"])
        self.assertNotIn("chat-response", names)
        self.assertEqual(emitted[-2]["body"], GUARDRAIL_NOTICE)

        # Exactly one audit record, the event, with no content on it. The
        # canned refusal is neither audited as Penny's answer nor kept in
        # the session, so the next turn cannot replay it.
        self.assertEqual([r.kind for r in audit.records], ["guardrail_blocked"])
        self.assertEqual(audit.records[0].reference, "req-bedrock-42")
        self.assertEqual((audit.records[0].client_query, audit.records[0].ai_response), ("", ""))
        self.assertEqual(sessions.history(session.session_id), [])
