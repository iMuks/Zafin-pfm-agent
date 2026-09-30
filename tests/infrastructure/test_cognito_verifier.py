"""The JWKS contract: cache at startup, one rate-limited refresh on an unknown
kid, 503 (never 401) when nothing is cached and the fetch fails.

Keys are generated here; nothing is fetched over the network.
"""

from __future__ import annotations

import time
import unittest

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from penny.domain.errors import InvalidTokenError, JwksUnavailableError
from penny.infrastructure.auth.cognito import CognitoTokenVerifier

ISSUER = "https://cognito-idp.ca-central-1.amazonaws.com/ca-central-1_TestPool"
CLIENT = "1234567890abcdefghij"


def keypair(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    import json

    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return pem, jwk


def token(pem: bytes, kid: str, **claims):
    payload = {
        "sub": "user-1",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        "token_use": "access",
        "client_id": CLIENT,
    }
    payload.update(claims)
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Verifier(unittest.TestCase):
    def setUp(self):
        self.pem, self.jwk = keypair("kid-1")
        self.jwks = {"keys": [self.jwk]}
        self.fetches = 0
        self.clock = Clock()

    def fetch(self):
        self.fetches += 1
        return self.jwks

    def make(self, fetch=None):
        return CognitoTokenVerifier(
            issuer=ISSUER,
            app_client_id=CLIENT,
            fetch_jwks=fetch or self.fetch,
            refresh_min_seconds=60,
            clock=self.clock,
        )

    def test_a_valid_access_token_yields_its_subject(self):
        verifier = self.make()
        identity = verifier.verify(token(self.pem, "kid-1"))
        self.assertEqual((identity.poid, identity.token_use), ("user-1", "access"))
        self.assertEqual(verifier.status, "ok")

    def test_an_id_token_is_checked_against_aud(self):
        identity = self.make().verify(
            token(self.pem, "kid-1", token_use="id", aud=CLIENT, client_id=None)
        )
        self.assertEqual(identity.token_use, "id")

    def test_wrong_client_expired_and_wrong_issuer_are_401(self):
        verifier = self.make()
        for bad in (
            token(self.pem, "kid-1", client_id="someone-else"),
            token(self.pem, "kid-1", exp=int(time.time()) - 10),
            token(self.pem, "kid-1", iss="https://evil.example"),
            token(self.pem, "kid-1", token_use="refresh"),
        ):
            with self.assertRaises(InvalidTokenError):
                verifier.verify(bad)

    def test_a_forged_signature_is_401(self):
        other_pem, _ = keypair("kid-1")
        with self.assertRaises(InvalidTokenError):
            self.make().verify(token(other_pem, "kid-1"))

    def test_an_unknown_kid_refreshes_once_and_then_accepts_the_rotated_key(self):
        verifier = self.make()
        new_pem, new_jwk = keypair("kid-2")
        self.jwks = {"keys": [new_jwk]}  # the pool rotated
        self.clock.now += 61
        identity = verifier.verify(token(new_pem, "kid-2"))
        self.assertEqual(identity.poid, "user-1")
        self.assertEqual(self.fetches, 2)

    def test_the_refresh_is_rate_limited(self):
        verifier = self.make()
        new_pem, new_jwk = keypair("kid-2")
        self.jwks = {"keys": [new_jwk]}
        self.clock.now += 10  # inside the minimum interval: no refresh, so 401
        with self.assertRaises(InvalidTokenError):
            verifier.verify(token(new_pem, "kid-2"))
        self.assertEqual(self.fetches, 1)

    def test_empty_cache_and_failed_fetch_is_503_not_401(self):
        def down():
            raise ConnectionError("egress blocked")

        verifier = self.make(fetch=down)
        self.assertEqual(verifier.status, "unavailable")
        with self.assertRaises(JwksUnavailableError) as caught:
            verifier.verify(token(self.pem, "kid-1"))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertGreater(caught.exception.retry_after_seconds, 0)

    def test_a_failed_refresh_keeps_the_cached_keys(self):
        verifier = self.make()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            raise ConnectionError("transient")

        verifier._fetch = flaky  # the next refresh fails
        self.clock.now += 61
        with self.assertRaises(InvalidTokenError):
            verifier.verify(token(self.pem, "kid-unknown"))
        self.assertEqual(verifier.status, "ok")
        self.assertEqual(verifier.verify(token(self.pem, "kid-1")).poid, "user-1")
