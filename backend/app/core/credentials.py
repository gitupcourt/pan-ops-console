"""Tiny auth helpers replacing the old credentials service.

The data model now stores an encrypted API key directly on each Device / Panorama
row. These helpers just hide the Fernet round-trip and the username/password →
keygen → key flow so the route handlers stay short.
"""

from __future__ import annotations

from app.core.command_proxy.pan_client import keygen
from app.core.crypto import decrypt, encrypt


def decrypt_key(blob: bytes | None) -> str:
    if not blob:
        raise ValueError("no API key stored")
    return decrypt(blob)


def encrypt_key(key: str) -> bytes:
    return encrypt(key)


def mint_key(host: str, username: str, password: str, *, verify_tls: bool = True) -> bytes:
    """Run keygen against `host` and return the encrypted key, ready to store."""
    key = keygen(host, username, password, verify_tls=verify_tls)
    return encrypt_key(key)
