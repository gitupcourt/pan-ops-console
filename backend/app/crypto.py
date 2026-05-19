"""Symmetric encryption for credentials at rest.

Wraps Fernet so callers don't import cryptography directly. The key is loaded
from settings.FERNET_KEY at process start.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_settings = get_settings()
_fernet = Fernet(_settings.FERNET_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode("utf-8")


__all__ = ["encrypt", "decrypt", "InvalidToken"]
