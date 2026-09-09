"""The LangGraph workflow, driven end to end with a fake chat model.

No API key and no network: the fake model returns a scripted JSONL answer, so
what is under test is the graph itself — stage order, component extraction,
session persistence, and history replay on the following turn.
"""

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from penny.infrastructure.llm.chat_runtime import answer_text
from penny.infrastructure.persistence.memory_sessions import InMemorySessionRepository
from tests.support import FakeChatModel, chat_runtime, request_context

ANSWER = (
    '{"component":"chat-response","body":"June groceries came to $80.50."}\n'
    '{"component":"bar-chart","valueTitle":"Spend ($)","labels":["Total"],'
    '"data":[{"label":"May","values":[120.0]},{"label":"Jun","values":[80.5]}]}\n'
    '{"component":"suggested-user-intents","intents":["Compare to May"]}\n'
)


class Turn(unittest.IsolatedAsyncioTestCase):
    async def _run(self, script, *, sessions=None, question="How much on groceries in June?"):
        sessions = sessions or InMemorySessionRepository(1800)
        ctx = request_context()
        session = sessions.get_or_create(ctx.poid, None)
        ctx.session_id = session.session_id

        runtime = chat_runtime(FakeChatModel(messages=iter([script])), sessions=sessions)
        emitted = [
            component
            async for component in runtime.stream(message=question, session_id=session.session_id)
        ]
        return sessions, session.session_id, emitted

    async def test_components_stream_in_reading_order(self):
        _, _, emitted = await self._run(ANSWER)
        self.assertEqual(
            [c["component"] for c in emitted],
            ["chat-response", "bar-chart", "suggested-user-intents", "feedback", "done"],
        )

    async def test_turn_closes_with_telemetry(self):
        _, _, emitted = await self._run(ANSWER)
        self.assertEqual(emitted[-1]["component"], "done")

    async def test_both_turns_are_persisted_as_prose(self):
        sessions, session_id, _ = await self._run(ANSWER)
        history = sessions.history(session_id)
        self.assertEqual([t.role for t in history], ["user", "assistant"])
        # Stored as text, never as raw JSONL: replaying the wire format teaches
        # the model to quote its own envelope back.
        self.assertEqual(history[1].content, "June groceries came to $80.50.")

    async def test_history_reaches_the_following_turn(self):
        sessions, session_id, _ = await self._run(ANSWER)
        ctx = request_context()
        ctx.session_id = session_id
        runtime = chat_runtime(FakeChatModel(messages=iter([ANSWER])), sessions=sessions)
        async for _ in runtime.stream(message="and May?", session_id=session_id):
            pass
        self.assertEqual(len(sessions.history(session_id)), 4)

    async def test_a_prose_answer_still_surfaces_something(self):
        """If the model ignores the format, the customer must not see nothing."""
        _, _, emitted = await self._run("Sorry, I could not work that out.")
        self.assertIn("try-again-error", [c["component"] for c in emitted])

    async def test_malformed_component_degrades_but_the_rest_renders(self):
        script = (
            '{"component":"table","headers":["a","b"],"data":[["only-one"]]}\n'
            '{"component":"chat-response","body":"here you go"}\n'
        )
        _, _, emitted = await self._run(script)
        names = [c["component"] for c in emitted]
        self.assertIn("unsupported", names)
        self.assertIn("chat-response", names)


class AnswerRecovery(unittest.TestCase):
    def test_chat_response_bodies_are_joined(self):
        message = AIMessage(
            content=(
                '{"component":"chat-response","body":"one"}\n'
                '{"component":"bar-chart","valueTitle":"x","labels":["s"],"data":[]}\n'
                '{"component":"chat-response","body":"two"}\n'
            )
        )
        self.assertEqual(answer_text([message]), "one\ntwo")

    def test_block_list_content_is_read(self):
        """The shape that silently emptied session history on the first live run.

        With thinking enabled the content is a list of blocks; a str-only read
        returned "" and the assistant turn was persisted blank, so the next turn
        had no memory of it.
        """
        message = AIMessage(
            content=[
                {"type": "thinking", "thinking": "internal reasoning"},
                {"type": "text", "text": '{"component":"chat-response","body":"from blocks"}\n'},
            ]
        )
        self.assertEqual(answer_text([message]), "from blocks")

    def test_falls_back_to_raw_prose(self):
        self.assertEqual(answer_text([AIMessage(content="plain words")]), "plain words")

    def test_empty_history_yields_an_empty_string(self):
        self.assertEqual(answer_text([]), "")


if __name__ == "__main__":
    unittest.main()
