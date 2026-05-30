"""Tiny auth helpers replacing the old credentials service.

The data model now stores an encrypted API key directly on each Device / Panorama
row. These helpers just hide the Fernet round-trip and the username/password →
keygen → key flow so the route handlers stay short.
"""

from __future__ import annotations

import logging

from app.core.command_proxy.pan_client import keygen
from app.core.crypto import decrypt, encrypt

# AppSec F-4: every FERNET decrypt of a stored secret is an audit event.
# decrypt_key is the single chokepoint — device/panorama API keys, OIDC
# client secrets, and TOTP secrets all decrypt through here — so one log
# call here covers the whole credential surface. Dedicated logger name so
# the platform's log pipeline can route / alert on it independently.
# This is the detection-visibility v1 (the review's "highest-leverage
# detection control"); a durable, queryable audit table with full actor
# attribution is the platform-coordinated follow-up.
_audit = logging.getLogger("audit.crypto")


def decrypt_key(blob: bytes | None, *, purpose: str = "unspecified") -> str:
    """Decrypt a stored secret. `purpose` identifies WHAT is being
    decrypted (e.g. "device:5", "panorama:2", "oidc:entra", "totp") so
    the audit line is actionable — "why was device 5's key decrypted at
    3am?" is answerable from the log alone. Callers should always pass it.
    """
    if not blob:
        raise ValueError("no API key stored")
    _audit.info("fernet_decrypt purpose=%s", purpose)
    return decrypt(blob)


def encrypt_key(key: str) -> bytes:
    return encrypt(key)


def mint_key(host: str, username: str, password: str, *, verify_tls: bool = True) -> bytes:
    """Run keygen against `host` and return the encrypted key, ready to store."""
    key = keygen(host, username, password, verify_tls=verify_tls)
    return encrypt_key(key)
