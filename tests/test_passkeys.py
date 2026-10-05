from __future__ import annotations

import hashlib
import json
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from app.config import Settings
from app.main import create_app
from app.passkeys import android_origins

ORIGIN = "https://assistant.example.test"
TOKEN = {"X-Assistant-Approval-Token": "approval-secret"}


class SoftAuthenticator:
    """A platform authenticator in software: what Touch ID does, minus the finger."""

    def __init__(self, rp_id: str = "assistant.example.test", origin: str = ORIGIN):
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"soft-credential-1"
        self.count = 0

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        self.count += 1
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.count) + attested

    def create(self, options: dict) -> dict:
        numbers = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose_key
        )
        auth_data = self._auth_data(0x45, attested)  # user present + verified + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        credential_id = bytes_to_base64url(self.credential_id)
        return {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self._client_data("webauthn.create", options["challenge"])
                ),
                "attestationObject": bytes_to_base64url(attestation),
            },
        }

    def get(self, options: dict, *, flags: int = 0x05) -> str:
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = self._auth_data(flags)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        credential_id = bytes_to_base64url(self.credential_id)
        return json.dumps(
            {
                "id": credential_id,
                "rawId": credential_id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": bytes_to_base64url(client_data),
                    "authenticatorData": bytes_to_base64url(auth_data),
                    "signature": bytes_to_base64url(signature),
                },
            }
        )


@pytest.fixture
def app_client(database_url: str):
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=True,
            approval_token="approval-secret",
            public_url=ORIGIN,
        )
    )
    with TestClient(app) as client:
        yield client
    app.state.database.dispose()


def _register(client: TestClient, authenticator: SoftAuthenticator, headers=TOKEN):
    options = client.post("/passkeys/registration-options").json()
    return client.post(
        "/passkeys",
        headers=headers,
        json={"credential": authenticator.create(options), "name": "Mac"},
    )


def _approve(client: TestClient, authenticator: SoftAuthenticator, method: str, path: str):
    options = client.post("/passkeys/approval-options", json={"method": method, "path": path})
    assert options.status_code == 200, options.text
    return authenticator.get(options.json())


def _remove_attempt(client: TestClient, header: dict) -> int:
    return client.delete("/passkeys/missing", headers=header).status_code


def test_registering_a_passkey_needs_the_approval_token(app_client: TestClient) -> None:
    authenticator = SoftAuthenticator()

    assert _register(app_client, authenticator, headers={}).status_code == 401
    assert _register(app_client, authenticator).status_code == 201

    (passkey,) = app_client.get("/passkeys").json()["passkeys"]
    assert passkey["name"] == "Mac"


def test_a_passkey_approves_exactly_the_request_it_was_signed_for(
    app_client: TestClient,
) -> None:
    authenticator = SoftAuthenticator()
    _register(app_client, authenticator)

    signed = _approve(app_client, authenticator, "DELETE", "/passkeys/missing")
    # Signed for this request: accepted (404 is the route itself answering).
    assert _remove_attempt(app_client, {"X-Assistant-Passkey": signed}) == 404
    # Replayed: refused.
    assert _remove_attempt(app_client, {"X-Assistant-Passkey": signed}) == 401

    elsewhere = _approve(app_client, authenticator, "DELETE", "/passkeys/other")
    assert _remove_attempt(app_client, {"X-Assistant-Passkey": elsewhere}) == 401


def test_a_passkey_without_user_verification_is_refused(app_client: TestClient) -> None:
    authenticator = SoftAuthenticator()
    _register(app_client, authenticator)
    options = app_client.post(
        "/passkeys/approval-options", json={"method": "DELETE", "path": "/passkeys/missing"}
    ).json()

    presence_only = authenticator.get(options, flags=0x01)

    assert _remove_attempt(app_client, {"X-Assistant-Passkey": presence_only}) == 401


def test_a_passkey_from_another_site_is_refused(app_client: TestClient) -> None:
    authenticator = SoftAuthenticator()
    _register(app_client, authenticator)
    phishing = SoftAuthenticator(origin="https://evil.example.test")
    phishing.key, phishing.count = authenticator.key, authenticator.count

    signed = _approve(app_client, phishing, "DELETE", "/passkeys/missing")

    assert _remove_attempt(app_client, {"X-Assistant-Passkey": signed}) == 401


def test_the_android_app_origin_comes_from_its_signing_certificate() -> None:
    fingerprint = ":".join(["AB"] * 32)

    (origin,) = android_origins([fingerprint])

    assert origin.startswith("android:apk-key-hash:")
    assert base64url_to_bytes(origin.split(":", 2)[2]) == bytes([0xAB] * 32)


def test_without_a_public_url_passkeys_are_off(client: TestClient) -> None:
    assert client.get("/passkeys").json() == {"enabled": False, "passkeys": []}
    assert client.post("/passkeys/registration-options").status_code == 409
