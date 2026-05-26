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

from app.core.command_proxy.pan_client import (
    TargetDisconnectedError,
    _friendly_check_error,
    _looks_like_target_disconnect,
)


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


# ---------- TargetDisconnectedError classification ----------
#
# These tests pin the "is this exception Panorama saying the target is
# offline, or is it Panorama itself being unreachable?" decision boundary.
# The classifier feeds the proxy-vs-direct fallback logic in
# `command_proxy.builder` — getting it wrong here corrupts
# `panoramas.reachable` for every device behind a healthy Panorama whose
# firewall happens to be offline (the N×1 attribution bug from #50).


def test_target_disconnect_detects_target_not_connected():
    assert _looks_like_target_disconnect(Exception("target not connected"))
    assert _looks_like_target_disconnect(Exception("Target is not connected"))


def test_target_disconnect_detects_named_device_not_connected():
    # The most common real-world shape from PAN-OS XML API.
    assert _looks_like_target_disconnect(
        Exception("Device '0123456789' is not connected")
    )


def test_target_disconnect_detects_unknown_target():
    assert _looks_like_target_disconnect(
        Exception("target serial does not exist on this Panorama")
    )
    assert _looks_like_target_disconnect(Exception("No such target"))
    assert _looks_like_target_disconnect(Exception("target unknown"))


def test_target_disconnect_detects_target_connection_failed():
    assert _looks_like_target_disconnect(
        Exception("Target connection failed: retry later")
    )


def test_target_disconnect_does_not_match_network_errors():
    # Network-level failures to Panorama itself should NOT classify as
    # target-disconnect — they're Panorama health issues.
    assert not _looks_like_target_disconnect(Exception("Connection refused"))
    assert not _looks_like_target_disconnect(Exception("Connection timed out"))
    assert not _looks_like_target_disconnect(
        Exception("[Errno -2] Name or service not known")
    )
    assert not _looks_like_target_disconnect(
        Exception("HTTP 401 unauthorized — bad credential")
    )
    assert not _looks_like_target_disconnect(
        Exception("certificate verify failed: self-signed certificate")
    )


def test_target_disconnect_does_not_match_unrelated_errors():
    assert not _looks_like_target_disconnect(
        Exception("Internal Server Error 500")
    )
    assert not _looks_like_target_disconnect(
        Exception("PAN-OS reported invalid command syntax")
    )


def test_target_disconnected_error_is_a_connection_error():
    """Callers that catch `ConnectionError` (the broad case) still catch
    TargetDisconnectedError — the inheritance is what keeps existing
    `except ConnectionError` blocks working unchanged."""
    assert issubclass(TargetDisconnectedError, ConnectionError)
    exc = TargetDisconnectedError("nope")
    assert isinstance(exc, ConnectionError)
    assert isinstance(exc, TargetDisconnectedError)


# ---------- context kwarg + keygen-path patterns ----------
#
# pan-ops-console#46: operator added a device with a hostname that didn't
# resolve in DNS; the form returned "keygen failed: [Errno -2] Name or
# service not known" — technically correct, but the average operator
# doesn't know -2 means DNS. The context kwarg lets the helper produce
# a message that names what was being attempted, so the operator can
# disambiguate "I was trying to authenticate to a brand-new device"
# from "the daily readiness scan failed."


def test_context_kwarg_prefixes_message():
    msg = _friendly_check_error(
        Exception("Connection refused"), context="Authentication"
    )
    assert msg.startswith("Authentication:")
    # The body still contains the same actionable hint.
    assert "TCP/443" in msg


def test_context_default_preserves_legacy_readiness_text():
    """Existing callers in the codebase pass no context — the default
    'Readiness checks' must keep producing 'Readiness checks failed: ...'
    in the generic fallback so older callers and tests stay valid."""
    msg = _friendly_check_error(Exception("something else"))
    assert "Readiness checks failed: something else" in msg


def test_keygen_dns_errno_negative_2_matches():
    """The exact shape the operator saw on pan-ops-console#46 —
    glibc/POSIX `getaddrinfo` returns -2 (EAI_NONAME) when the hostname
    can't be resolved. httpx surfaces it verbatim in ConnectError."""
    msg = _friendly_check_error(
        Exception("[Errno -2] Name or service not known"),
        context="Authentication",
    )
    assert msg.startswith("Authentication:")
    assert "DNS lookup failed" in msg
    assert "hostname" in msg.lower()
    # Original error still present so the operator can grep logs.
    assert "Errno -2" in msg


def test_keygen_dns_errno_negative_3_also_matches():
    """`Errno -3` is `EAI_AGAIN` — DNS server returned a temporary
    failure (often a SERVFAIL or upstream timeout). Same fix space as -2,
    same message branch."""
    msg = _friendly_check_error(
        Exception("[Errno -3] Temporary failure in name resolution"),
        context="Authentication",
    )
    assert "DNS lookup failed" in msg


def test_keygen_invalid_credential_xml_response():
    """When PAN-OS replies with status=error and a message like
    'Invalid Credential' (the most common keygen rejection), the
    helper routes it to the credential-hint branch."""
    msg = _friendly_check_error(
        Exception("Invalid Credential"), context="Authentication"
    )
    assert msg.startswith("Authentication:")
    assert "Authentication rejected" in msg
    assert "XML API access" in msg
