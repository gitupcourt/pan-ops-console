"""AppSec F-4: every FERNET decrypt of a stored secret emits an audit log.

decrypt_key is the single chokepoint (device/panorama keys, OIDC client
secrets, TOTP secrets all route through it), so we assert the audit
event fires there with the caller-supplied purpose, and that a real
round-trip still returns the plaintext.

We verify the contract by swapping the module's `_audit` logger for a
fake recorder. Capturing via the live logging framework (caplog or a
direct handler) proved flaky under pytest's logging plugin — it manages
levels / `logging.disable` around each test, which intercepts the
record before any handler sees it. Asserting `decrypt_key` calls
`_audit.info(purpose=...)` tests exactly what F-4 promises, with zero
dependence on logging config.
"""

from __future__ import annotations

import pytest

from app.core import credentials


class _FakeAudit:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


@pytest.fixture
def fake_audit(monkeypatch):
    fake = _FakeAudit()
    monkeypatch.setattr(credentials, "_audit", fake)
    return fake


def test_decrypt_key_emits_audit_event(fake_audit):
    blob = credentials.encrypt_key("super-secret-api-key")
    out = credentials.decrypt_key(blob, purpose="device:42")
    assert out == "super-secret-api-key"
    assert len(fake_audit.messages) == 1
    assert "device:42" in fake_audit.messages[0]


def test_decrypt_key_audit_does_not_leak_plaintext(fake_audit):
    """The audit line records WHAT was decrypted, never the secret."""
    blob = credentials.encrypt_key("do-not-log-me")
    credentials.decrypt_key(blob, purpose="panorama:1")
    assert fake_audit.messages
    for m in fake_audit.messages:
        assert "do-not-log-me" not in m


def test_decrypt_key_missing_blob_raises_without_audit(fake_audit):
    """No blob → ValueError before any decrypt; nothing to audit."""
    with pytest.raises(ValueError):
        credentials.decrypt_key(None, purpose="device:1")
    assert fake_audit.messages == []
