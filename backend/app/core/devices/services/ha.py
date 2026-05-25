"""HA-state mapping helpers, shared between probe / sync / orchestrator.

Single source of truth for translating Panorama's raw HA state strings
into our HARole enum. Carry-forward from pan-fw-upgrader's
panorama_sync.map_ha_role at phase 4c-services-precheck — kept here in
core so probe_device (in upgrade.services.precheck) and the eventual
panorama_sync (in core.panorama.services) don't drift.
"""

from __future__ import annotations

from app.core.devices.models.enums import HARole


def map_ha_role(ha_state: str | None) -> HARole:
    """Translate Panorama's HA state string into the HARole enum.

    Panorama emits raw strings like 'active', 'passive', 'active-primary',
    'active-secondary', 'tentative', 'non-functional', 'initial',
    'suspended', 'standby', or null/empty for non-HA devices. We collapse
    them into the four-state enum the upgrade orchestrator's drivers
    consume.
    """
    if not ha_state:
        return HARole.STANDALONE
    s = ha_state.lower()
    if "active" in s:
        return HARole.ACTIVE
    if "passive" in s or "standby" in s:
        return HARole.PASSIVE
    return HARole.UNKNOWN
