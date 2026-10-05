"""Registering passkeys and asking for a passkey-signed approval."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.common import approval_action, require_approval_token
from app.passkeys import PasskeyError, PasskeyService

router = APIRouter()


class PasskeyRegistration(BaseModel):
    credential: dict[str, Any]
    name: str = Field(default="Passkey", max_length=80)


class ApprovalChallengeRequest(BaseModel):
    method: str = Field(pattern="^(POST|PUT|PATCH|DELETE)$")
    path: str = Field(pattern="^/[A-Za-z0-9/_.-]{1,400}$")


def _passkeys(request: Request) -> PasskeyService:
    return request.app.state.passkeys


def _conflict(error: PasskeyError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.get("/passkeys")
def list_passkeys(request: Request) -> dict[str, Any]:
    service = _passkeys(request)
    return {
        "enabled": service.enabled,
        "passkeys": [
            {
                "id": passkey.id,
                "name": passkey.name,
                "created_at": passkey.created_at,
                "last_used_at": passkey.last_used_at,
            }
            for passkey in (service.list() if service.enabled else [])
        ],
    }


@router.post("/passkeys/registration-options")
def registration_options(request: Request) -> dict[str, Any]:
    try:
        return _passkeys(request).registration_options()
    except PasskeyError as error:
        raise _conflict(error) from error


@router.post("/passkeys", status_code=201)
def register_passkey(body: PasskeyRegistration, request: Request) -> dict[str, str]:
    require_approval_token(request)
    try:
        passkey = _passkeys(request).register(body.credential, name=body.name)
    except PasskeyError as error:
        raise _conflict(error) from error
    return {"id": passkey.id, "name": passkey.name}


@router.delete("/passkeys/{passkey_id}", status_code=204)
def remove_passkey(passkey_id: str, request: Request) -> None:
    require_approval_token(request)
    if not _passkeys(request).remove(passkey_id):
        raise HTTPException(status_code=404, detail="Passkey not found")


@router.post("/passkeys/approval-options")
def approval_options(body: ApprovalChallengeRequest, request: Request) -> dict[str, Any]:
    """A one-time challenge for approving exactly this request with a passkey."""
    try:
        return _passkeys(request).approval_options(approval_action(body.method, body.path))
    except PasskeyError as error:
        raise _conflict(error) from error
