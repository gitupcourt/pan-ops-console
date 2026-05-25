"""Tests for the Docker-subnet collision detector in connectivity.py.

This rule is the single most useful diagnostic the panel produces — it
catches the failure mode where a firewall's mgmt IP lies inside a
Docker-internal subnet, which makes the container unable to reach the
device even though the host can. Without this hint the operator chases
firewall rules, NAT, and DNS for hours.
"""

from __future__ import annotations

from app.services.connectivity import _check_docker_collision


def test_docker_desktop_vpnkit_subnet_flagged():
    hint = _check_docker_collision("192.168.65.129")
    assert hint is not None
    assert "192.168.65.0/24" in hint
    assert "Docker Desktop" in hint


def test_default_bridge_flagged():
    hint = _check_docker_collision("172.17.0.42")
    assert hint is not None
    assert "172.17.0.0/16" in hint


def test_compose_default_bridge_flagged():
    hint = _check_docker_collision("172.18.0.6")
    assert hint is not None
    assert "172.18.0.0/16" in hint


def test_regular_lan_address_not_flagged():
    """A typical home/office LAN should pass through clean — no false alarms."""
    assert _check_docker_collision("10.0.0.5") is None
    assert _check_docker_collision("192.168.1.1") is None
    assert _check_docker_collision("192.168.50.10") is None


def test_invalid_ip_returns_none():
    """We get the resolved IP from getaddrinfo so this shouldn't happen,
    but be defensive — don't crash the diagnostic on garbage input."""
    assert _check_docker_collision("not an ip") is None
    assert _check_docker_collision("") is None


def test_172_16_below_docker_range_not_flagged():
    """172.16.0.0/12 is broader than what Docker reserves; only 172.17–31
    are Docker. 172.16.x should NOT be flagged — it's a valid corporate net."""
    assert _check_docker_collision("172.16.0.1") is None


def test_172_32_above_docker_range_not_flagged():
    assert _check_docker_collision("172.32.0.1") is None
