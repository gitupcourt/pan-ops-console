"""Helpers for turning a stored Credential row into something a client can use.

Mirrors pan-fw-upgrader's services/credentials.py so a future merge of the two
apps doesn't have to reconcile two different credential resolution shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.crypto import decrypt
from app.models.credential import Credential
from app.models.enums import AuthType


@dataclass
class ResolvedCredential:
    """Plaintext credential material. Never stored, never logged."""

    auth_type: AuthType
    api_key: str | None = None
    username: str | None = None
    password: str | None = None


def resolve(cred: Credential) -> ResolvedCredential:
    plaintext = decrypt(cred.encrypted_secret)
    if cred.auth_type == AuthType.API_KEY:
        return ResolvedCredential(auth_type=cred.auth_type, api_key=plaintext)
    data = json.loads(plaintext)
    return ResolvedCredential(
        auth_type=cred.auth_type,
        username=data.get("username"),
        password=data.get("password"),
    )


def encode_secret(auth_type: AuthType, *, api_key: str | None = None,
                  username: str | None = None, password: str | None = None) -> str:
    """Build the plaintext blob to be encrypted before DB insert."""
    if auth_type == AuthType.API_KEY:
        if not api_key:
            raise ValueError("api_key required for AuthType.API_KEY")
        return api_key
    if not (username and password):
        raise ValueError("username and password required for AuthType.USERPASS")
    return json.dumps({"username": username, "password": password})
