"""The tolerant JSONL extractor.

Every case below was observed in real model output at least once. Splitting on
"\n" and calling json.loads would lose an entire turn to any one of them, which
is why the parser counts braces instead.
"""

from __future__ import annotations

import unittest

from penny.infrastructure.llm.streaming.jsonl import extract_json_objects

CHAT = '{"component":"chat-response","body":"a"}'


class TolerantExtraction(unittest.TestCase):
    def test_clean_lines(self):
        objects, rest = extract_json_objects(f"{CHAT}\n{CHAT}\n")
        self.assertEqual(len(objects), 2)
        self.assertEqual(rest, "")

    def test_stray_prose_between_objects(self):
        objects, _ = extract_json_objects(f"here you go:\n{CHAT}\nblah\n{CHAT}\n")
        self.assertEqual(len(objects), 2)

    def test_eos_token_is_stripped(self):
        objects, _ = extract_json_objects(f"{CHAT}<|end|>\n")
        self.assertEqual(len(objects), 1)

    def test_missing_newline_separator(self):
        """Brace counting, not a line split: two objects share one line."""
        objects, _ = extract_json_objects(f"{CHAT}{CHAT}")
        self.assertEqual(len(objects), 2)

    def test_raw_newline_inside_a_string_is_repaired(self):
        objects, _ = extract_json_objects('{"component":"chat-response","body":"l1\nl2"}\n')
        self.assertEqual(objects[0]["body"], "l1\nl2")

    def test_truncated_tail_is_retained_for_the_next_delta(self):
        objects, rest = extract_json_objects(f'{CHAT}\n{{"component":"tab')
        self.assertEqual(len(objects), 1)
        self.assertEqual(rest, '{"component":"tab')

    def test_escaped_quotes_survive(self):
        objects, _ = extract_json_objects(
            '{"component":"chat-response","body":"she said \\"hi\\""}\n'
        )
        self.assertEqual(objects[0]["body"], 'she said "hi"')

    def test_unparseable_object_is_dropped_and_the_next_kept(self):
        objects, _ = extract_json_objects('{"component":,,,}\n{"component":"feedback"}\n')
        self.assertEqual([o["component"] for o in objects], ["feedback"])

    def test_nested_braces_in_values(self):
        objects, _ = extract_json_objects(
            '{"component":"bar-chart","data":[{"label":"Jul","values":[1.5]}]}\n'
        )
        self.assertEqual(len(objects[0]["data"]), 1)

    def test_arrives_character_by_character(self):
        """The real delivery pattern: deltas land mid-object."""
        buffer, emitted = "", []
        stream = f'{CHAT}\n{{"component":"suggested-user-intents","intents":["More"]}}\n'
        for char in stream:
            buffer += char
            objects, buffer = extract_json_objects(buffer)
            emitted += objects
        self.assertEqual(
            [o["component"] for o in emitted], ["chat-response", "suggested-user-intents"]
        )


if __name__ == "__main__":
    unittest.main()
