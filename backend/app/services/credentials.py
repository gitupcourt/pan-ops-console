"""Helpers for turning a stored Credential row into something a client can use."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.crypto import decrypt
from app.models.credential import Credential
from app.models.enums import AuthType


@dataclass
class ResolvedCredential:
    """Plaintext credential material. Never stored, never logged. Lives only in memory."""

    auth_type: AuthType
    api_key: str | None = None
    username: str | None = None
    password: str | None = None


def resolve(cred: Credential) -> ResolvedCredential:
    """Decrypt the credential's secret blob and return its components."""
    plaintext = decrypt(cred.encrypted_secret)
    if cred.auth_type == AuthType.API_KEY:
        return ResolvedCredential(auth_type=cred.auth_type, api_key=plaintext)
    data = json.loads(plaintext)
    return ResolvedCredential(
        auth_type=cred.auth_type,
        username=data.get("username"),
        password=data.get("password"),
    )
