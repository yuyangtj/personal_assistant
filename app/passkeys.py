"""Approving with a passkey (Touch ID, a phone's fingerprint) instead of the long token.

Every approval signs a fresh, one-time challenge that the server issued for that exact
request ("POST /tasks/<id>/pull-request-approval"), so a captured approval can't be replayed
or pointed at another action. Registering a passkey needs the approval token itself (or an
existing passkey), so getting past the site login alone can't enrol a device.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import webauthn
from sqlalchemy import delete, select
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    parse_authentication_credential_json,
    parse_client_data_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.persistence.database import Database
from app.persistence.models import PasskeyChallengeModel, PasskeyModel

CHALLENGE_LIFETIME = timedelta(minutes=5)
REGISTER = "register"
APPROVE = "approve"


class PasskeyError(ValueError):
    pass


def android_origins(sha256_fingerprints: Iterable[str]) -> list[str]:
    """The origins Android reports for an app signed with these certificates."""
    return [
        "android:apk-key-hash:" + bytes_to_base64url(bytes.fromhex(fingerprint.replace(":", "")))
        for fingerprint in sha256_fingerprints
    ]


class PasskeyService:
    def __init__(
        self,
        database: Database,
        *,
        public_url: str | None,
        extra_origins: Iterable[str] = (),
        now=lambda: datetime.now(UTC),
    ):
        self.database = database
        self.now = now
        parts = urlsplit(public_url or "")
        self.rp_id = parts.hostname or ""
        self.origins = [f"{parts.scheme}://{parts.netloc}", *extra_origins] if self.rp_id else []

    @property
    def enabled(self) -> bool:
        return bool(self.rp_id)

    def list(self) -> list[PasskeyModel]:
        with self.database.session() as session:
            return list(session.scalars(select(PasskeyModel).order_by(PasskeyModel.created_at)))

    def remove(self, passkey_id: str) -> bool:
        with self.database.session() as session, session.begin():
            removed = session.execute(delete(PasskeyModel).where(PasskeyModel.id == passkey_id))
            return removed.rowcount > 0

    def registration_options(self) -> dict[str, Any]:
        self._require_enabled()
        options = webauthn.generate_registration_options(
            rp_id=self.rp_id,
            rp_name="Personal Assistant",
            user_id=b"owner",
            user_name="owner",
            user_display_name="Personal Assistant owner",
            challenge=self._issue(REGISTER, ""),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=self._descriptors(),
        )
        return json.loads(webauthn.options_to_json(options))

    def register(self, credential: dict[str, Any], *, name: str) -> PasskeyModel:
        self._require_enabled()
        challenge = self._challenge_of(credential)
        self._consume(challenge, REGISTER, "")
        try:
            verified = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=self.rp_id,
                expected_origin=self.origins,
                require_user_verification=True,
            )
        except Exception as error:  # the library raises several types for a bad credential
            raise PasskeyError(f"The passkey could not be verified: {error}") from error
        passkey = PasskeyModel(
            id=bytes_to_base64url(verified.credential_id),
            name=(name.strip() or "Passkey")[:80],
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
        )
        with self.database.session() as session, session.begin():
            if session.get(PasskeyModel, passkey.id) is not None:
                raise PasskeyError("This passkey is already registered")
            session.add(passkey)
        return passkey

    def approval_options(self, action: str) -> dict[str, Any]:
        self._require_enabled()
        descriptors = self._descriptors()
        if not descriptors:
            raise PasskeyError("No passkey is registered")
        options = webauthn.generate_authentication_options(
            rp_id=self.rp_id,
            challenge=self._issue(APPROVE, action),
            allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return json.loads(webauthn.options_to_json(options))

    def verify_approval(self, assertion: str, action: str) -> PasskeyModel:
        """Accept a signed assertion for exactly this request, once."""
        self._require_enabled()
        try:
            credential = parse_authentication_credential_json(assertion)
            challenge = bytes_to_base64url(
                parse_client_data_json(credential.response.client_data_json).challenge
            )
        except Exception as error:
            raise PasskeyError("The passkey response is malformed") from error
        self._consume(challenge, APPROVE, action)
        with self.database.session() as session, session.begin():
            passkey = session.get(PasskeyModel, credential.id, with_for_update=True)
            if passkey is None:
                raise PasskeyError("This passkey is not registered")
            try:
                verified = webauthn.verify_authentication_response(
                    credential=credential,
                    expected_challenge=base64url_to_bytes(challenge),
                    expected_rp_id=self.rp_id,
                    expected_origin=self.origins,
                    credential_public_key=passkey.public_key,
                    credential_current_sign_count=passkey.sign_count,
                    require_user_verification=True,
                )
            except Exception as error:
                raise PasskeyError(f"The passkey could not be verified: {error}") from error
            passkey.sign_count = verified.new_sign_count
            passkey.last_used_at = self.now()
            return passkey

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise PasskeyError("Passkeys need ASSISTANT_PUBLIC_URL to be set")

    def _descriptors(self) -> list[PublicKeyCredentialDescriptor]:
        return [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.id))
            for passkey in self.list()
        ]

    def _issue(self, purpose: str, action: str) -> bytes:
        challenge = secrets.token_bytes(32)
        with self.database.session() as session, session.begin():
            session.execute(
                delete(PasskeyChallengeModel).where(PasskeyChallengeModel.expires_at < self.now())
            )
            session.add(
                PasskeyChallengeModel(
                    challenge=bytes_to_base64url(challenge),
                    purpose=purpose,
                    action=action,
                    expires_at=self.now() + CHALLENGE_LIFETIME,
                )
            )
        return challenge

    def _consume(self, challenge: str, purpose: str, action: str) -> None:
        """Use up a challenge; it is spent even if the signature then fails."""
        with self.database.session() as session, session.begin():
            row = session.get(PasskeyChallengeModel, challenge, with_for_update=True)
            if row is None or row.used or row.purpose != purpose:
                raise PasskeyError("Unknown or already used passkey challenge")
            expires_at = row.expires_at
            if expires_at.tzinfo is None:  # SQLite drops the time zone
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < self.now():
                raise PasskeyError("The passkey challenge expired; try again")
            if row.action != action:
                raise PasskeyError("This passkey approval was made for a different action")
            row.used = True

    @staticmethod
    def _challenge_of(credential: dict[str, Any]) -> str:
        try:
            client_data = base64url_to_bytes(credential["response"]["clientDataJSON"])
            return bytes_to_base64url(parse_client_data_json(client_data).challenge)
        except Exception as error:
            raise PasskeyError("The passkey response is malformed") from error
