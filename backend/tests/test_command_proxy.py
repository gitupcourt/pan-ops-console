"""Tests for app.core.command_proxy.build_client_with_fallback.

Covers the four routes the contract names:
1. proxy preconditions met + proxy healthy → returns proxy client,
   marks Panorama reachable=True
2. proxy preconditions met + proxy fails + device can direct → returns
   direct client, marks Panorama reachable=False with error message
3. proxy preconditions met + proxy fails + device CANNOT direct →
   raises ConnectionError, leaves Panorama unhealthy
4. proxy preconditions not met (proxy_via_panorama=False) → returns
   direct client without touching Panorama health

The PanDeviceClient construction itself is mocked at module level —
we're testing the routing logic + side effects, not pan-os-python's
network behavior.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.command_proxy import build_client_with_fallback
from app.core.devices.models.device import Device
from app.core.panorama.models.panorama import Panorama


KEY = "0iJL2gP4XzVnQ5OYG9w7c-3RbWUf3jM0SQk5oN6E9Bs="


def _seed_panorama(db, name: str = "pano1") -> Panorama:
    fernet = Fernet(KEY.encode())
    pano = Panorama(
        name=name,
        hostname="10.0.0.100",
        encrypted_api_key=fernet.encrypt(b"pano-key"),
        verify_tls=False,
        reachable=True,
    )
    db.add(pano)
    db.commit()
    db.refresh(pano)
    return pano


def _seed_device(
    db,
    name: str = "dev1",
    *,
    proxy_via_panorama: bool = False,
    panorama: Panorama | None = None,
    serial: str | None = None,
    has_own_key: bool = True,
) -> Device:
    fernet = Fernet(KEY.encode())
    dev = Device(
        name=name,
        hostname="10.0.0.1",
        ip_address="10.0.0.1",
        serial=serial,
        verify_tls=False,
        proxy_via_panorama=proxy_via_panorama,
        polling_enabled=True,
        panorama_id=panorama.id if panorama else None,
        encrypted_api_key=fernet.encrypt(b"dev-key") if has_own_key else None,
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def test_proxy_healthy_returns_proxy_and_marks_panorama_reachable(client, db):
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=True, panorama=pano, serial="0123456789",
    )

    # PanDeviceClient.via_panorama returns a stub whose get_system_info
    # succeeds — proxy path is healthy.
    class _OK:
        def get_system_info(self):
            return {}

    with patch(
        "app.core.command_proxy.builder.PanDeviceClient.via_panorama",
        return_value=_OK(),
    ) as via:
        client_obj, route = build_client_with_fallback(db, dev)

    assert route == "proxy"
    assert client_obj.get_system_info() == {}
    via.assert_called_once()

    db.refresh(pano)
    assert pano.reachable is True
    assert pano.last_reachability_error is None
    assert isinstance(pano.last_reachability_at, datetime)


def test_proxy_fails_falls_back_to_direct_and_marks_panorama_unhealthy(client, db):
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=True, panorama=pano, serial="0123456789",
        has_own_key=True,  # has direct fallback
    )

    class _Broken:
        def get_system_info(self):
            raise ConnectionError("panorama timeout")

    class _DirectOK:
        pass

    with patch(
        "app.core.command_proxy.builder.PanDeviceClient.via_panorama",
        return_value=_Broken(),
    ), patch(
        "app.core.command_proxy.builder.PanDeviceClient.direct",
        return_value=_DirectOK(),
    ) as direct_factory:
        client_obj, route = build_client_with_fallback(db, dev)

    assert route == "direct"
    assert isinstance(client_obj, _DirectOK)
    direct_factory.assert_called_once()

    db.refresh(pano)
    assert pano.reachable is False
    assert "panorama timeout" in pano.last_reachability_error
    assert isinstance(pano.last_reachability_at, datetime)


def test_proxy_fails_no_direct_fallback_raises_connection_error(client, db):
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=True, panorama=pano, serial="0123456789",
        has_own_key=False,  # no direct fallback
    )

    class _Broken:
        def get_system_info(self):
            raise ConnectionError("panorama timeout")

    with patch(
        "app.core.command_proxy.builder.PanDeviceClient.via_panorama",
        return_value=_Broken(),
    ):
        with pytest.raises(ConnectionError, match="no direct route available"):
            build_client_with_fallback(db, dev)

    db.refresh(pano)
    assert pano.reachable is False  # Still marked unhealthy


def test_direct_only_device_skips_proxy_path(client, db):
    """proxy_via_panorama=False → go direct, never call via_panorama,
    never touch Panorama health."""
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=False, panorama=pano, serial="0123456789",
    )

    class _Direct:
        pass

    with patch(
        "app.core.command_proxy.builder.PanDeviceClient.via_panorama",
    ) as via, patch(
        "app.core.command_proxy.builder.PanDeviceClient.direct",
        return_value=_Direct(),
    ):
        client_obj, route = build_client_with_fallback(db, dev)

    assert route == "direct"
    assert isinstance(client_obj, _Direct)
    via.assert_not_called()

    db.refresh(pano)
    # Panorama health untouched — should still be the seed-default True
    assert pano.reachable is True
    assert pano.last_reachability_at is None
    assert pano.last_reachability_error is None


def test_proxy_preconditions_missing_serial_skips_proxy(client, db):
    """proxy_via_panorama=True but no serial → preconditions not met,
    falls through to direct without trying proxy."""
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=True, panorama=pano, serial=None,
        has_own_key=True,
    )

    class _Direct:
        pass

    with patch(
        "app.core.command_proxy.builder.PanDeviceClient.via_panorama",
    ) as via, patch(
        "app.core.command_proxy.builder.PanDeviceClient.direct",
        return_value=_Direct(),
    ):
        client_obj, route = build_client_with_fallback(db, dev)

    assert route == "direct"
    via.assert_not_called()


def test_direct_device_with_no_key_raises_meaningfully(client, db):
    """Direct path requires the device's own encrypted_api_key."""
    pano = _seed_panorama(db)
    dev = _seed_device(
        db, proxy_via_panorama=False, panorama=pano, serial=None,
        has_own_key=False,
    )

    with pytest.raises(ValueError, match="no API key for direct"):
        build_client_with_fallback(db, dev)
