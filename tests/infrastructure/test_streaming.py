"""Chunk processing: model text in, validated components out."""

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from penny.infrastructure.llm.streaming.handler import StreamingResponseHandler, text_of
from penny.infrastructure.observability.telemetry import StreamTelemetry


class TextExtraction(unittest.TestCase):
    def test_plain_string_content(self):
        self.assertEqual(text_of(AIMessage(content="hello")), "hello")

    def test_block_list_content(self):
        """Live responses arrive as blocks, not a string."""
        message = AIMessage(content=[{"type": "text", "text": "hello"}])
        self.assertEqual(text_of(message), "hello")

    def test_thinking_blocks_are_excluded(self):
        """A thinking block in the JSONL buffer would corrupt the whole turn."""
        message = AIMessage(
            content=[
                {"type": "thinking", "thinking": "let me work this out"},
                {"type": "text", "text": '{"component":"chat-response","body":"x"}'},
            ]
        )
        self.assertEqual(text_of(message), '{"component":"chat-response","body":"x"}')

    def test_unknown_shape_yields_empty_string(self):
        self.assertEqual(text_of(object()), "")


class Handler(unittest.TestCase):
    def setUp(self):
        self.telemetry = StreamTelemetry(model_id="fake")
        self.handler = StreamingResponseHandler(self.telemetry)

    def test_components_emerge_as_they_complete(self):
        first = self.handler.process('{"component":"chat-response","body":"part')
        self.assertEqual(first, [])  # incomplete: nothing may be emitted yet
        second = self.handler.process('ial"}\n')
        self.assertEqual([c["component"] for c in second], ["chat-response"])

    def test_malformed_component_degrades_to_unsupported(self):
        emitted = self.handler.process(
            '{"component":"table","headers":["a","b"],"data":[["one"]]}\n'
        )
        self.assertEqual([c["component"] for c in emitted], ["unsupported"])

    def test_server_owned_components_are_not_accepted_from_the_model(self):
        """The model must not be able to fabricate transaction cards."""
        emitted = self.handler.process('{"component":"transaction-list","transactions":[]}\n')
        self.assertEqual(emitted, [])
        self.assertEqual(self.telemetry.dropped_lines, 1)

    def test_non_component_json_is_counted_as_dropped(self):
        self.assertEqual(self.handler.process('{"thinking":"..."}\n'), [])
        self.assertEqual(self.telemetry.dropped_lines, 1)

    def test_finalize_discards_an_incomplete_tail(self):
        self.handler.process('{"component":"chat-response","body":"cut off')
        self.assertEqual(self.handler.finalize(), [])
        self.assertGreaterEqual(self.telemetry.dropped_lines, 1)

    def test_telemetry_records_the_first_component(self):
        self.handler.process('{"component":"chat-response","body":"x"}\n')
        self.assertIsNotNone(self.telemetry.ttfc_ms)
        self.assertEqual(self.telemetry.components, 1)


if __name__ == "__main__":
    unittest.main()
