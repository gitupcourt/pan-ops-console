"""AppSec F-4: every FERNET decrypt of a stored secret emits an audit log.

decrypt_key is the single chokepoint (device/panorama keys, OIDC client
secrets, TOTP secrets all route through it), so we assert the audit
event fires there with the caller-supplied purpose, and that a real
round-trip still returns the plaintext.

We capture by attaching a handler directly to the `audit.crypto` logger
rather than via pytest's caplog — caplog depends on propagation to the
root handler, which the app's logging config can interfere with. A
dedicated handler on the named logger is config-independent.
"""

from __future__ import annotations

import contextlib
import logging

import pytest

from app.core.credentials import decrypt_key, encrypt_key


@contextlib.contextmanager
def _capture_audit():
    """Yield a list that collects records emitted on `audit.crypto`."""
    logger = logging.getLogger("audit.crypto")
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector()
    prev_level = logger.level
    # Another test in the suite can leave a global `logging.disable()` set,
    # which short-circuits Logger.info BEFORE any handler runs — that's why
    # neither caplog nor a direct handler saw the record. Clear it for the
    # duration and restore after.
    prev_disable = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logging.disable(prev_disable)


def test_decrypt_key_emits_audit_event():
    blob = encrypt_key("super-secret-api-key")
    with _capture_audit() as records:
        out = decrypt_key(blob, purpose="device:42")
    assert out == "super-secret-api-key"
    assert len(records) == 1
    assert "device:42" in records[0].getMessage()


def test_decrypt_key_audit_does_not_leak_plaintext():
    """The audit line records WHAT was decrypted, never the secret."""
    blob = encrypt_key("do-not-log-me")
    with _capture_audit() as records:
        decrypt_key(blob, purpose="panorama:1")
    assert records
    for r in records:
        assert "do-not-log-me" not in r.getMessage()


def test_decrypt_key_missing_blob_raises_without_audit():
    """No blob → ValueError before any decrypt; nothing to audit."""
    with _capture_audit() as records:
        with pytest.raises(ValueError):
            decrypt_key(None, purpose="device:1")
    assert records == []
