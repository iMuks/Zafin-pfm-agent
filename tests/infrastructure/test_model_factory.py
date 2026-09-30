"""Model construction: capability flags and the structured-output rule.

Both behaviours here were live 400s or silent failures before they were tests.
The registry override is used throughout so these assertions do not depend on
whatever `.env` happens to set locally.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from langchain_openai import ChatOpenAI

from penny.domain.errors import RemoteModelForbiddenError
from penny.infrastructure.config.models import MODEL_REGISTRY, enforce_enclave
from penny.infrastructure.config.settings import settings
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


class LocalProvider(unittest.TestCase):
    """The enclave rule, enforced where the client is built.

    A `local` entry must produce a client that talks to the configured in-house
    endpoint, and it must refuse any configuration that would send a request
    outside the deployment: a vendor-hosted model tag, or a public hostname.
    """

    def setUp(self):
        settings.cache_clear()

    def tearDown(self):
        settings.cache_clear()

    def test_local_entries_are_marked_local_and_reject_effort(self):
        for key in ("local-qwen3-4b", "local-qwen3-8b", "local-qwen25-7b"):
            self.assertEqual(MODEL_REGISTRY[key].provider, "local", key)
            self.assertFalse(MODEL_REGISTRY[key].supports_effort, key)

    def test_anthropic_entries_default_to_the_anthropic_provider(self):
        for key in ("opus5", "sonnet5", "haiku45"):
            self.assertEqual(MODEL_REGISTRY[key].provider, "anthropic", key)

    def test_local_model_targets_the_configured_endpoint(self):
        with mock.patch.dict(os.environ, {"PENNY_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1"}):
            settings.cache_clear()
            model = chat_model("chat", override="local-qwen3-4b")
        self.assertIsInstance(model, ChatOpenAI)
        self.assertEqual(model.openai_api_base, "http://127.0.0.1:11434/v1")
        body = payload(model)
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertNotIn("thinking", body)
        self.assertNotIn("output_config", body)

    def test_thinking_flag_is_ignored_for_local_models(self):
        model = chat_model("judge", override="local-qwen3-4b", thinking=False)
        self.assertNotIn("thinking", payload(model))

    def test_a_vendor_hosted_tag_is_refused(self):
        with self.assertRaises(RemoteModelForbiddenError):
            enforce_enclave("kimi-k2.5:cloud", "http://127.0.0.1:11434/v1")

    def test_a_public_endpoint_is_refused_by_default(self):
        with self.assertRaises(RemoteModelForbiddenError):
            enforce_enclave("qwen3:4b", "https://api.example.com/v1")

    def test_private_hosts_are_accepted(self):
        for url in (
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/v1",
            "http://10.0.3.7:8000/v1",
            "http://192.168.1.20:8000/v1",
            "http://vllm.internal:8000/v1",
            "http://models.svc:8000/v1",
        ):
            enforce_enclave("qwen3:4b", url)  # must not raise

    def test_public_endpoint_can_be_allowed_only_explicitly(self):
        enforce_enclave("qwen3:4b", "https://models.example.com/v1", allow_public_host=True)

    def test_public_endpoint_from_settings_is_refused(self):
        with mock.patch.dict(os.environ, {"PENNY_LOCAL_BASE_URL": "https://api.example.com/v1"}):
            settings.cache_clear()
            with self.assertRaises(RemoteModelForbiddenError):
                chat_model("chat", override="local-qwen3-4b")


if __name__ == "__main__":
    unittest.main()
