"""Authentication at the HTTP boundary in `cognito` mode.

A stub verifier stands in for Cognito: what is under test is the middleware's
contract, which paths require a token, and what a missing key set returns.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from penny.application.ports.auth import Identity
from penny.composition.container import container
from penny.domain.errors import InvalidTokenError, JwksUnavailableError
from penny.presentation.http.app import create_app
from tests.presentation.test_http import StubChat, StubGreeting


class StubVerifier:
    def __init__(self, status: str = "ok") -> None:
        self.status = status
        self.seen: list[str] = []

    def verify(self, token: str) -> Identity:
        self.seen.append(token)
        if self.status == "unavailable":
            raise JwksUnavailableError(45)
        if token != "good":
            raise InvalidTokenError
        return Identity(poid="sub-1", subject="sub-1", token_use="access")


class CognitoMode(unittest.TestCase):
    def setUp(self):
        container.override(chat=StubChat(), greeting=StubGreeting())
        self.verifier = StubVerifier()
        self.client = TestClient(create_app(verifier=self.verifier))

    def tearDown(self):
        container.override(chat=None, greeting=None)

    def test_healthz_needs_no_token_and_says_only_liveness(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "jwks": "ok"})
        self.assertEqual(self.verifier.seen, [])

    def test_api_health_is_401_without_a_token(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 401)
        self.assertIn("x-request-id", response.headers)

    def test_chat_is_401_with_a_bad_token(self):
        response = self.client.post(
            "/agent/chat", json={"message": "hi"}, headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(response.status_code, 401)

    def test_a_good_token_reaches_the_route_scoped_to_its_subject(self):
        response = self.client.post(
            "/agent/chat", json={"message": "hi"}, headers={"Authorization": "Bearer good"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.verifier.seen, ["good"])

    def test_the_static_client_loads_without_a_token(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_missing_key_set_is_503_with_retry_after(self):
        client = TestClient(create_app(verifier=StubVerifier(status="unavailable")))
        response = client.get("/api/health", headers={"Authorization": "Bearer good"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "45")
        self.assertEqual(client.get("/healthz").json()["jwks"], "unavailable")


class NoneMode(unittest.TestCase):
    """Mode `none` is the laptop: no verifier, the development identity, /healthz says n/a."""

    def setUp(self):
        container.override(chat=StubChat(), greeting=StubGreeting())
        self.client = TestClient(create_app())

    def tearDown(self):
        container.override(chat=None, greeting=None)

    def test_healthz_reports_no_verifier(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok", "jwks": "n/a"})

    def test_api_health_is_open_and_names_the_mode(self):
        body = self.client.get("/api/health").json()
        self.assertEqual((body["auth_mode"], body["jwks"]), ("none", "n/a"))
