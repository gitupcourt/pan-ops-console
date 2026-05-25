"""Symmetric encryption for credentials at rest.

Wraps Fernet so callers don't import cryptography directly. The key is loaded
from settings.FERNET_KEY at process start.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_settings = get_settings()
_fernet = Fernet(_settings.FERNET_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    """Encrypt a string. Returns ciphertext bytes suitable for DB storage."""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt ciphertext bytes back to a string. Raises InvalidToken if tampered/wrong key."""
    return _fernet.decrypt(ciphertext).decode("utf-8")


__all__ = ["encrypt", "decrypt", "InvalidToken"]
