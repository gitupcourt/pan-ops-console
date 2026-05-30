"""AppSec F-4: every FERNET decrypt of a stored secret emits an audit log.

decrypt_key is the single chokepoint (device/panorama keys, OIDC client
secrets, TOTP secrets all route through it), so we assert the audit
event fires there with the caller-supplied purpose, and that a real
round-trip still returns the plaintext.
"""

from __future__ import annotations

import logging

from app.core.credentials import decrypt_key, encrypt_key


def test_decrypt_key_emits_audit_event(caplog):
    blob = encrypt_key("super-secret-api-key")
    with caplog.at_level(logging.INFO, logger="audit.crypto"):
        out = decrypt_key(blob, purpose="device:42")
    assert out == "super-secret-api-key"
    # Exactly one audit record, carrying the purpose, on the audit logger.
    recs = [r for r in caplog.records if r.name == "audit.crypto"]
    assert len(recs) == 1
    assert "device:42" in recs[0].getMessage()


def test_decrypt_key_audit_does_not_leak_plaintext(caplog):
    """The audit line records WHAT was decrypted, never the secret."""
    blob = encrypt_key("do-not-log-me")
    with caplog.at_level(logging.INFO, logger="audit.crypto"):
        decrypt_key(blob, purpose="panorama:1")
    for r in caplog.records:
        assert "do-not-log-me" not in r.getMessage()


def test_decrypt_key_missing_blob_raises_without_audit(caplog):
    """No blob → ValueError before any decrypt; nothing to audit."""
    import pytest

    with caplog.at_level(logging.INFO, logger="audit.crypto"):
        with pytest.raises(ValueError):
            decrypt_key(None, purpose="device:1")
    assert [r for r in caplog.records if r.name == "audit.crypto"] == []
