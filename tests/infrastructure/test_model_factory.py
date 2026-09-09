"""Model construction: capability flags and the structured-output rule.

Both behaviours here were live 400s or silent failures before they were tests.
The registry override is used throughout so these assertions do not depend on
whatever `.env` happens to set locally.
"""

from __future__ import annotations

import unittest

from penny.infrastructure.config.models import MODEL_REGISTRY
from penny.infrastructure.llm.model_factory import chat_model


def payload(model):
    return model._get_request_payload([{"role": "user", "content": "hi"}], stop=None)


class Capabilities(unittest.TestCase):
    def test_haiku_is_marked_as_rejecting_effort(self):
        """Haiku 4.5 answers `output_config.effort` with a 400."""
        self.assertFalse(MODEL_REGISTRY["haiku45"].supports_effort)

    def test_the_opus_and_sonnet_entries_accept_effort(self):
        for key in ("opus5", "sonnet5"):
            self.assertTrue(MODEL_REGISTRY[key].supports_effort, key)

    def test_effort_is_omitted_for_a_model_that_rejects_it(self):
        self.assertNotIn("output_config", payload(chat_model("chat", override="haiku45")))

    def test_effort_is_sent_for_a_model_that_accepts_it(self):
        self.assertIn("output_config", payload(chat_model("chat", override="opus5")))


class Thinking(unittest.TestCase):
    def test_thinking_is_adaptive_by_default(self):
        self.assertEqual(payload(chat_model("chat"))["thinking"], {"type": "adaptive"})

    def test_thinking_off_is_explicit_rather_than_omitted(self):
        """Current models think by default, so omitting the parameter is not "off"."""
        self.assertEqual(
            payload(chat_model("judge", thinking=False))["thinking"], {"type": "disabled"}
        )

    def test_temperature_is_never_sent(self):
        """Current Anthropic models reject sampling parameters outright."""
        self.assertNotIn("temperature", payload(chat_model("chat")))


if __name__ == "__main__":
    unittest.main()
