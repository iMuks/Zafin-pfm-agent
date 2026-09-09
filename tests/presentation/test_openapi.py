"""The published API contract.

An OpenAPI document that disagrees with the wire is worse than none: a client
generated from it fails in ways the generator cannot warn about. These tests
pin the two things that were actually wrong before they were written — the
streamed endpoints were advertised as `application/json`, and the component
line shapes were not published at all.
"""

from __future__ import annotations

import unittest

from pydantic import TypeAdapter

from penny.application.components.contract import CONTRACT
from penny.presentation.http.app import create_app
from penny.presentation.http.schemas import ComponentLine


class Spec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = create_app().openapi()

    def _response(self, path: str, method: str = "post"):
        return self.spec["paths"][path][method]["responses"]["200"]

    def test_streaming_endpoints_are_not_advertised_as_json(self):
        """They send newline-delimited JSON; a JSON media type misleads clients."""
        for path, method in (("/agent/chat", "post"), ("/agent/greeting", "get")):
            content = self._response(path, method)["content"]
            self.assertIn("text/plain", content, path)
            self.assertNotIn("application/json", content, path)

    def test_health_publishes_a_referenced_response_model(self):
        schema = self._response("/api/health", "get")["content"]["application/json"]["schema"]
        self.assertEqual(schema["$ref"], "#/components/schemas/HealthResponse")

    def test_session_and_request_headers_are_documented(self):
        """A consumer cannot continue a conversation without knowing about these."""
        headers = self._response("/agent/chat")["headers"]
        self.assertIn("x-session-id", headers)
        self.assertIn("x-request-id", headers)

    def test_streamed_line_schema_is_published_and_self_contained(self):
        schema = self._response("/agent/chat")["content"]["text/plain"]["schema"]
        line = schema["x-ndjson-line-schema"]
        self.assertIn("oneOf", line)
        # Nested models must resolve inside the line schema, not against the
        # parent document, or the schema cannot be lifted out and reused.
        self.assertIn("$defs", line)

    def test_request_body_documents_the_production_envelope(self):
        body = self.spec["paths"]["/agent/chat"]["post"]["requestBody"]
        self.assertIn("application/json", body["content"])

    def test_endpoints_carry_summaries_and_tags(self):
        for path, method in (
            ("/agent/chat", "post"),
            ("/agent/greeting", "get"),
            ("/api/health", "get"),
        ):
            operation = self.spec["paths"][path][method]
            self.assertTrue(operation.get("summary"), path)
            self.assertTrue(operation.get("tags"), path)


class NoDrift(unittest.TestCase):
    def test_published_models_match_the_component_contract(self):
        """The wire contract has one definition; this proves the docs track it.

        `CONTRACT` is the authority. The published union adds exactly one case —
        `unsupported`, which is emitted when validation fails rather than being
        a component the server deliberately produces.
        """
        published = {
            variant["properties"]["component"]["const"]
            for variant in TypeAdapter(ComponentLine).json_schema()["$defs"].values()
            if "properties" in variant and "const" in variant["properties"].get("component", {})
        }
        self.assertEqual(published, set(CONTRACT) | {"unsupported"})


if __name__ == "__main__":
    unittest.main()
