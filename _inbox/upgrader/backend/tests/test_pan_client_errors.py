"""Tests for the friendly error remapping in pan_client.

The raw exceptions from pan-os-python and urllib are technically correct
but operationally opaque — "URLError: [Errno 111]" doesn't tell the
operator whether to check DNS, the management profile, the worker's
network path, or the credential. `_friendly_check_error` maps each
common shape to an actionable hint.

Regressions here would silently degrade the operator experience for
brand-new device additions — exactly the moment when a clear error
matters most.
"""

from __future__ import annotations

from app.services.pan_client import _friendly_check_error


def test_connection_refused_maps_to_https_hint():
    msg = _friendly_check_error(
        Exception("URLError: reason: [Errno 111] Connection refused")
    )
    assert "TCP/443" in msg
    assert "HTTPS management" in msg
    # Original error is preserved so the operator can still grep logs.
    assert "Errno 111" in msg


def test_timeout_maps_to_routing_hint():
    msg = _friendly_check_error(Exception("Connection timed out"))
    assert "Timed out" in msg
    assert "mgmt IP" in msg or "NAT" in msg


def test_dns_failure_maps_to_dns_hint():
    msg = _friendly_check_error(Exception("[Errno -2] Name or service not known"))
    assert "DNS lookup failed" in msg
    assert "management ip" in msg.lower()


def test_unauthorized_maps_to_credential_hint():
    msg = _friendly_check_error(Exception("HTTP 401 unauthorized"))
    assert "Authentication rejected" in msg
    assert "XML API access" in msg


def test_invalid_credential_text_also_maps_to_credential_hint():
    msg = _friendly_check_error(Exception("Invalid credential supplied"))
    assert "Authentication rejected" in msg


def test_our_tls_handshake_failure_maps_to_verify_tls_hint():
    """Genuine cert-verify failure between us and the firewall — verify_tls
    is the right knob to suggest."""
    msg = _friendly_check_error(Exception("certificate verify failed: self-signed certificate"))
    assert "verify_tls" in msg
    assert "TLS handshake to the firewall" in msg


def test_firewall_outbound_ssl_not_mistaken_for_our_tls():
    """The user case that prompted this rewrite: the LIBRARY reports an
    SSL failure that's actually the firewall's outbound connection to
    updates.paloaltonetworks.com. verify_tls=false won't help — saying
    it would actively misleads the operator."""
    msg = _friendly_check_error(Exception(
        "Failed to check Content content upgrade info due to SSL connect error. "
        "Please check network connectivity and try again."
    ))
    # The message MUST steer the operator away from verify_tls. We say so
    # explicitly ("verify_tls won't help"), so the substring is allowed only
    # in that exact negative framing.
    assert "verify_tls won't help" in msg
    assert "TLS handshake to the firewall" not in msg
    assert "firewall's own outbound" in msg
    assert "updates.paloaltonetworks.com" in msg


def test_bare_ssl_word_does_not_trip_our_tls_branch_strict():
    """Sanity: a generic 'ssl' substring without 'certificate verify failed'
    etc. should NOT produce the verify_tls-fixable hint."""
    msg = _friendly_check_error(Exception("some other ssl thing"))
    assert "TLS handshake to the firewall" not in msg


def test_bare_ssl_word_does_not_trip_our_tls_branch():
    """Defensive: a generic 'ssl' substring shouldn't push the operator
    toward verify_tls unless we're sure it's the handshake."""
    msg = _friendly_check_error(Exception("ssl error doing something"))
    # Falls through to generic — not the TLS-handshake hint.
    assert "verify_tls" not in msg
    assert "Readiness checks failed" in msg


def test_unrecognized_falls_back_to_generic():
    msg = _friendly_check_error(Exception("something else entirely"))
    assert "Readiness checks failed" in msg
    assert "something else entirely" in msg
