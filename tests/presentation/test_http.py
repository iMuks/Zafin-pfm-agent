"""The HTTP delivery layer.

Runs against stub runtimes injected through the composition root — the point of
having Protocol seams. No API key, no model call, no network.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from penny.composition.container import container
from penny.presentation.http.app import create_app
from penny.presentation.http.middleware import poid_from_headers

ROOT = Path(__file__).resolve().parents[2]


class StubChat:
    """An `AgentRuntime` that replays a fixed component script."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def stream(self, *, message: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((message, session_id))
        yield {"component": "chat-response", "body": f"echo: {message}"}
        yield {"component": "done"}


class StubGreeting:
    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        yield {"component": "chat-response", "body": "hello"}
        yield {"component": "done"}


class ExplodingChat:
    async def stream(self, *, message: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
        yield {"component": "chat-response", "body": "partial"}
        raise RuntimeError("model connection dropped")


def read_components(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@unittest.skipUnless(
    (ROOT / "data" / "transactions_enriched.json").exists(),
    "run scripts/enrich_transactions.py first",
)
class HttpApi(unittest.TestCase):
    def setUp(self):
        self.chat = StubChat()
        container.override(chat=self.chat, greeting=StubGreeting())
        self.client = TestClient(create_app())

    def tearDown(self):
        container.override(chat=None, greeting=None)

    def test_health_reports_model_tools_and_data(self):
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["tools"]), 10)
        self.assertIn("get_spending_by_category", body["tools"])
        self.assertEqual(body["data"]["transaction_count"], 300)

    def test_chat_accepts_the_production_envelope(self):
        response = self.client.post(
            "/agent/chat", json={"input": {"content": {"body": "How much on dining?"}}}
        )
        self.assertEqual(response.status_code, 200)
        components = read_components(response)
        self.assertEqual(components[0]["body"], "echo: How much on dining?")

    def test_chat_accepts_a_flat_body_too(self):
        response = self.client.post("/agent/chat", json={"message": "hi"})
        self.assertEqual(response.status_code, 200)

    def test_empty_message_is_rejected(self):
        self.assertEqual(self.client.post("/agent/chat", json={"message": "   "}).status_code, 422)

    def test_overlong_message_is_rejected(self):
        response = self.client.post("/agent/chat", json={"message": "x" * 5000})
        self.assertEqual(response.status_code, 422)

    def test_response_carries_a_session_id_that_round_trips(self):
        first = self.client.post("/agent/chat", json={"message": "one"})
        session_id = first.headers["x-session-id"]
        self.assertTrue(session_id.startswith("sess_"))

        self.client.post(
            "/agent/chat", json={"message": "two"}, headers={"x-session-id": session_id}
        )
        self.assertEqual(self.chat.calls[1][1], session_id, "second turn must reuse the session")

    def test_every_response_carries_a_request_id(self):
        self.assertIn("x-request-id", self.client.get("/api/health").headers)

    def test_each_line_is_one_complete_component(self):
        response = self.client.post("/agent/chat", json={"message": "hi"})
        for line in response.text.splitlines():
            if line.strip():
                self.assertIn("component", json.loads(line))

    def test_greeting_streams_components(self):
        components = read_components(self.client.get("/agent/greeting"))
        self.assertEqual(components[0]["body"], "hello")

    def test_a_mid_stream_failure_becomes_a_renderable_error(self):
        """Headers are already sent, so raising would truncate with no explanation."""
        container.override(chat=ExplodingChat())
        response = self.client.post("/agent/chat", json={"message": "boom"})
        self.assertEqual(response.status_code, 200)
        components = read_components(response)
        self.assertEqual(components[-1]["component"], "try-again-error")

    def test_the_phone_ui_is_served_at_the_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("phone", response.text)


class IdentityExtraction(unittest.TestCase):
    """The poid seam. NOT authentication — see the middleware docstring."""

    def test_falls_back_to_a_development_identity(self):
        self.assertEqual(poid_from_headers(None, None), "dev-customer")

    def test_reads_a_subject_from_an_unverified_bearer_token(self):
        import base64

        payload = base64.urlsafe_b64encode(b'{"sub":"cust-99"}').decode().rstrip("=")
        self.assertEqual(poid_from_headers(f"Bearer h.{payload}.sig", None), "cust-99")

    def test_malformed_tokens_degrade_rather_than_raise(self):
        self.assertEqual(poid_from_headers("Bearer not.a.jwt", None), "dev-customer")
        self.assertEqual(poid_from_headers(None, "!!!not-base64!!!"), "dev-customer")


if __name__ == "__main__":
    unittest.main()
