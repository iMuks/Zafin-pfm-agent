"""The Bedrock provider: the enclave rule at the endpoint, and Guardrails wiring.

No AWS credentials and no network: `ChatBedrockConverse` builds its boto3
client lazily enough to construct without either, so what is under test is
the configuration the factory hands it.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from penny.domain.errors import RemoteModelForbiddenError
from penny.infrastructure.config.models import enforce_bedrock_enclave
from penny.infrastructure.config.settings import settings
from penny.infrastructure.llm.model_factory import chat_model

VPCE = "https://vpce-0abc123def456-xyz789.bedrock-runtime.ca-central-1.vpce.amazonaws.com"
PUBLIC = "https://bedrock-runtime.ca-central-1.amazonaws.com"


class EnclaveGuard(unittest.TestCase):
    def test_the_vpc_interface_endpoint_is_accepted(self):
        enforce_bedrock_enclave(VPCE, "ca-central-1")

    def test_the_public_hostname_is_refused_even_if_private_dns_would_resolve_it(self):
        with self.assertRaises(RemoteModelForbiddenError):
            enforce_bedrock_enclave(PUBLIC, "ca-central-1")

    def test_an_endpoint_in_another_region_is_refused(self):
        with self.assertRaises(RemoteModelForbiddenError):
            enforce_bedrock_enclave(VPCE, "us-east-1")

    def test_an_unset_endpoint_is_refused(self):
        with self.assertRaises(RemoteModelForbiddenError):
            enforce_bedrock_enclave("", "ca-central-1")

    def test_the_override_is_explicit_and_only_for_fixtures(self):
        enforce_bedrock_enclave(PUBLIC, "ca-central-1", allow_public_endpoint=True)


class _BedrockSettings:
    """Point the cached settings at a Bedrock configuration for one test."""

    def __init__(self, **overrides: str) -> None:
        env = {
            "PENNY_BEDROCK_REGION": "ca-central-1",
            "PENNY_BEDROCK_ENDPOINT_URL": VPCE,
            "PENNY_BEDROCK_GUARDRAIL_ID": "gr-1234",
            "PENNY_BEDROCK_GUARDRAIL_VERSION": "3",
            "PENNY_BEDROCK_ALLOW_PUBLIC_ENDPOINT": "false",
            "PENNY_BEDROCK_THINKING_BUDGET_TOKENS": "2048",
            "AWS_DEFAULT_REGION": "ca-central-1",
        }
        env.update(overrides)
        self._patch = mock.patch.dict(os.environ, env)

    def __enter__(self):
        self._patch.__enter__()
        settings.cache_clear()
        return self

    def __exit__(self, *exc):
        self._patch.__exit__(*exc)
        settings.cache_clear()


def _bedrock_settings(**overrides: str):
    return _BedrockSettings(**overrides)


class Factory(unittest.TestCase):
    def test_bedrock_entry_builds_a_converse_client_through_the_endpoint(self):
        with _bedrock_settings():
            model = chat_model("chat", override="bedrock-sonnet")
        self.assertEqual(type(model).__name__, "ChatBedrockConverse")
        self.assertEqual(model.endpoint_url, VPCE)
        self.assertEqual(model.region_name, "ca-central-1")

    def test_guardrails_are_passed_with_trace_enabled(self):
        with _bedrock_settings():
            model = chat_model("chat", override="bedrock-sonnet")
        self.assertEqual(
            model.guardrail_config,
            {"guardrailIdentifier": "gr-1234", "guardrailVersion": "3", "trace": "enabled"},
        )

    def test_thinking_is_a_request_field_and_off_for_structured_output(self):
        with _bedrock_settings():
            thinking = chat_model("chat", override="bedrock-sonnet")
            structured = chat_model("judge", override="bedrock-haiku", thinking=False)
        self.assertEqual(
            thinking.additional_model_request_fields,
            {"thinking": {"type": "enabled", "budget_tokens": 2048}},
        )
        self.assertIsNone(structured.additional_model_request_fields)

    def test_a_public_endpoint_never_produces_a_client(self):
        with (
            _bedrock_settings(PENNY_BEDROCK_ENDPOINT_URL=PUBLIC),
            self.assertRaises(RemoteModelForbiddenError),
        ):
            chat_model("chat", override="bedrock-sonnet")

    def test_no_api_key_is_required_for_bedrock(self):
        from penny.composition.container import Container

        with (
            _bedrock_settings(),
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}),
            mock.patch(
                "penny.composition.container.resolve_model",
                return_value=mock.Mock(provider="bedrock"),
            ),
        ):
            Container._require_credentials("chat")  # must not raise
