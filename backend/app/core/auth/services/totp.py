"""TOTP secret generation, verification, and backup-code management.

Wraps pyotp for the crypto bits and the app's Fernet helpers for
encryption-at-rest of the TOTP secret. Backup codes are stored as
SHA-256 hex hashes — losing the DB doesn't leak usable codes.

Provisioning URI follows the otpauth scheme (RFC-ish); both Google
Authenticator and Aegis / 1Password / Bitwarden accept it.
"""

from __future__ import annotations

import hashlib
import secrets

import pyotp

from app.core.credentials import decrypt_key, encrypt_key


# Backup codes are 10 hex chars (40 bits of entropy each). 10 codes
# total = 400 bits of attacker entropy across the set, far more than
# the 80-bit TOTP secret. Format is grouped XXXXX-XXXXX for readability.
_BACKUP_CODE_BYTES = 5         # 5 bytes = 10 hex chars
_BACKUP_CODE_COUNT = 10
_TOTP_ISSUER = "pan-capacity-analyzer"


def generate_secret() -> str:
    """A fresh base32 TOTP secret (160 bits). pyotp default."""
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> bytes:
    """Wrap base32 secret with the app's Fernet key for at-rest storage."""
    return encrypt_key(secret)


def decrypt_secret(blob: bytes) -> str:
    return decrypt_key(blob, purpose="totp")


def provisioning_uri(secret: str, username: str) -> str:
    """otpauth:// URI suitable for QR encoding. Authenticator apps render
    issuer + username as 'pan-capacity-analyzer: <user>'."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=_TOTP_ISSUER)


def verify_code(secret: str, code: str, *, window: int = 1) -> bool:
    """True if `code` is a valid 6-digit TOTP for `secret`.

    `window=1` accepts the current 30 s step and one before/after — covers
    minor clock skew between the user's phone and the server. pyotp
    defaults to window=0; we widen by 1 step (60 s of tolerance) which
    matches what most production apps do.
    """
    if not code or not code.isdigit() or len(code) not in (6, 8):
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=window)


# ---------- Backup codes ----------

def generate_backup_codes() -> list[str]:
    """Return a list of plaintext backup codes. Show these to the user
    exactly ONCE. The caller stores only their hashes."""
    return [
        f"{secrets.token_hex(_BACKUP_CODE_BYTES)[:5]}-{secrets.token_hex(_BACKUP_CODE_BYTES)[:5]}"
        for _ in range(_BACKUP_CODE_COUNT)
    ]


def hash_backup_code(plaintext: str) -> str:
    """Normalize + hash a backup code for storage / comparison.

    Strips dashes and whitespace and lowercases so users can type the
    code with or without the formatting we showed them at enrollment.
    """
    normalized = plaintext.strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
