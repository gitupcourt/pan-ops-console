"""Direct-to-device PAN-OS client wrapper.

Built on top of pan-os-python (Firewall / Panorama) and pan-os-upgrade-assurance
(FirewallProxy + CheckFirewall). Two construction paths:

* `PanDeviceClient.direct(...)`  — talk straight to the firewall's mgmt IP.
* `PanDeviceClient.via_panorama(...)` — talk to Panorama and ride its
  target-serial mechanism so the firewall's mgmt plane doesn't need to be
  reachable from us. Necessary for cloud devices and any device behind NAT.

Both paths produce a working FirewallProxy that:
- Capacity polling uses via `op_xml(cmd)` for arbitrary XML op commands
  from the metric catalog.
- The upgrade module (incoming phase 4c-services) uses via the higher-
  level methods (run_readiness_checks, take_snapshot, install_software,
  suspend_ha, ...).

Credential parameter shape: this version was lifted from pan-fw-upgrader
at phase 4c-services-pan-client and adapted to the merged app's inline
`encrypted_api_key` storage pattern — factories accept an `api_key`
string directly, not a `ResolvedCredential` dataclass. The credentials-
table-vs-inline reconciliation question stays deferred (see CLAUDE.md
phase-4a design contract).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from panos.errors import PanDeviceError
from panos.firewall import Firewall as PanosFirewall
from panos.panorama import Panorama as PanosPanorama
from panos_upgrade_assurance.check_firewall import CheckFirewall
from panos_upgrade_assurance.firewall_proxy import FirewallProxy

log = logging.getLogger(__name__)


class TargetDisconnectedError(ConnectionError):
    """The Panorama proxy was reachable but reported that the target firewall is offline.

    Distinct from a plain `ConnectionError` because the right reaction is
    different: Panorama itself is healthy (don't mark it unreachable), but
    the device behind it can't be reached *through Panorama* right now.

    Callers (notably `command_proxy.builder.build_client_with_fallback`)
    that decide where to attribute health-state side effects should catch
    this case separately. Plain `except ConnectionError` still catches it
    via the inheritance chain, so callers that don't care about the
    distinction keep working unchanged.
    """


# Substring patterns (lowercase) that indicate Panorama replied successfully
# but the *target device* is offline / unknown. These come from PAN-OS XML
# API error messages when you address a target serial through Panorama's
# `target=<serial>` mechanism. The exact wording varies slightly across
# PAN-OS versions; match liberally on the distinctive verbiage.
_TARGET_OFFLINE_PATTERNS: tuple[str, ...] = (
    "target not connected",
    "target is not connected",
    "is not connected",        # "device '0123' is not connected"
    "target connection failed",
    "target serial does not exist",
    "target unknown",
    "no such target",
)


def _looks_like_target_disconnect(exc: BaseException) -> bool:
    """True if the exception message indicates the target device is offline.

    Centralized here so the proxy-vs-device error attribution stays in one
    place — every PanDeviceError raising spot in this module can route
    through `_raise_op_error` below and get consistent classification.
    """
    msg = str(exc).lower()
    return any(p in msg for p in _TARGET_OFFLINE_PATTERNS)


def _raise_op_error(exc: BaseException, context: str) -> None:
    """Re-raise a PanDeviceError as either TargetDisconnectedError or ConnectionError.

    `context` is a short string describing what operation failed (e.g.
    "system info", "op() failed for cmd 'show foo'"). Used in the
    re-raised exception's message so traceback readers can tell which call
    site triggered the failure.

    Always raises — never returns. Return-type omitted so callers don't
    have to write `return _raise_op_error(...)` boilerplate.
    """
    if _looks_like_target_disconnect(exc):
        raise TargetDisconnectedError(f"{context}: {exc}") from exc
    raise ConnectionError(f"{context}: {exc}") from exc


# All readiness check names supported by panos_upgrade_assurance.CheckFirewall.
ALL_READINESS_CHECKS: list[str] = [
    "active_support",
    "arp_entry_exist",
    "candidate_config",
    "certificates_requirements",
    "config_locks",
    "content_version",
    "dp_cpu_utilization",
    "dynamic_updates",
    "expired_licenses",
    "free_disk_space",
    "global_jumbo_frame",
    "ha",
    "ip_sec_tunnel_status",
    "jobs",
    "mp_cpu_utilization",
    "mp_mem_utilization",
    "ntp_sync",
    "panorama",
    "planes_clock_sync",
    "session_exist",
    "environmentals",
]

DEFAULT_READINESS_CHECKS: list[str] = [
    "ha",
    "panorama",
    "candidate_config",
    "free_disk_space",
    "expired_licenses",
    "ntp_sync",
    "content_version",
    "dynamic_updates",
    "jobs",
]


@dataclass
class SystemInfo:
    """Fields we want for refreshing a Device row from a probe."""

    hostname: str | None
    serial: str | None
    model: str | None
    sw_version: str | None
    uptime: str | None
    app_version: str | None
    threat_version: str | None
    av_version: str | None
    wildfire_version: str | None
    url_filtering_version: str | None
    gp_client_version: str | None
    ha_state: str | None
    ha_sync_state: str | None
    ha_peer_serial: str | None


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def _friendly_check_error(exc: BaseException, *, context: str = "Readiness checks") -> str:
    """Turn raw pan-os-python / httpx / urllib exceptions into something operators can act on.

    The library raises a mix of `PanDeviceError`, `URLError`, `httpx.ConnectError`,
    `ssl.SSLError`, and bare `OSError` depending on where the failure happens. The
    default repr is something like "URLError: reason: [Errno 111] Connection refused"
    or "[Errno -2] Name or service not known" — technically correct but the operator
    has no idea whether to look at DNS, firewall, mgmt-profile, or credentials. We
    map the common shapes to actionable hints.

    `context` is a short noun phrase describing what was being attempted —
    "Readiness checks" (default), "Authentication" (from keygen), "Probe", etc.
    It's used in the fallback message AND prepended to specific-case messages
    so an operator reading the error knows whether the failure was during
    auth setup or during routine ops.
    """
    msg = str(exc)
    if "Connection refused" in msg or "[Errno 111]" in msg:
        return (
            f"{context}: Cannot reach device on TCP/443 (connection refused). "
            "Check that HTTPS management is enabled on the firewall's "
            "management interface profile and that the worker container "
            "has a network path to the mgmt IP. "
            f"Original error: {msg}"
        )
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return (
            f"{context}: Timed out connecting to the device. The mgmt IP "
            "may be wrong, or a firewall/NAT is dropping packets between "
            f"the worker and the device. Original error: {msg}"
        )
    if (
        "Name or service not known" in msg
        or "nodename nor servname" in msg
        # httpx wraps DNS failures in ConnectError("[Errno ...]") — match the errno too.
        or "[Errno -2]" in msg
        or "[Errno -3]" in msg
    ):
        return (
            f"{context}: DNS lookup failed for the device hostname. "
            "Either the hostname isn't in DNS, the cluster's DNS resolver "
            "can't reach it, or there's a typo. Try the management IP "
            f"directly, or add the hostname to DNS. Original error: {msg}"
        )
    if (
        "401" in msg
        # Case-insensitive — PAN-OS returns "Invalid Credential" (capital C),
        # while the existing test fixture used "Invalid credential" (lower c).
        # Match either to cover both shapes that arrive in practice.
        or "invalid credential" in msg.lower()
        or "unauthorized" in msg.lower()
    ):
        return (
            f"{context}: Authentication rejected by the device. Verify the "
            "credential (username/password or API key) and that the user "
            f"has XML API access. Original error: {msg}"
        )
    # Distinguish OUR TLS handshake to the firewall from the firewall's
    # OWN outbound TLS (e.g. it trying to reach updates.paloaltonetworks.com
    # for the content-version check). The former we can fix with
    # verify_tls=false; the latter we cannot, and saying so misleads the
    # operator into changing the wrong setting.
    low = msg.lower()
    is_our_handshake = (
        "certificate verify failed" in low
        or "self-signed" in low
        or "self signed" in low
        or "hostname mismatch" in low
        or "ssl handshake" in low
    )
    is_firewall_outbound = (
        "failed to check" in low
        or "upgrade info" in low
        or "update server" in low
    )
    if is_our_handshake and not is_firewall_outbound:
        return (
            f"{context}: TLS handshake to the firewall failed. If the "
            "firewall uses a self-signed cert, set verify_tls=false on "
            f"the device. Original error: {msg}"
        )
    if is_firewall_outbound:
        return (
            f"{context}: A library check could not reach an external service "
            "from the firewall (typically updates.paloaltonetworks.com). "
            "This is the firewall's own outbound connectivity, not the "
            "app's TLS to the firewall — verify_tls won't help here. Check "
            "the device's Update Server / Service Route / DNS settings. "
            f"Original error: {msg}"
        )
    return f"{context} failed: {msg}"


# Default per-call timeout (seconds) when a caller doesn't pass one. The
# builder threads the operator-configurable Settings.PAN_CLIENT_TIMEOUT_SECONDS
# through; this constant only governs direct callers (tests, ad-hoc helpers).
# See config.py for the rationale — it bounds a dead DIRECT device's connect
# instead of riding the ~127s OS TCP-connect default.
DEFAULT_OP_TIMEOUT_S = 30


def feature_train(version: str | None) -> tuple[int, int] | None:
    """Parse a PAN-OS version into its feature train (the first TWO numbers).

    ``'11.2.11' -> (11, 2)``; ``'12.1.4-h2' -> (12, 1)``; ``'10.2.0' -> (10, 2)``.
    Returns None if unparseable. A PAN-OS *major* upgrade crosses this X.Y
    boundary (11.1->11.2, 11.2->12.1) — not just the leading number — and that
    is the only case that needs the target train's base image pre-staged.
    """
    if not version:
        return None
    head = version.strip().split("-", 1)[0]  # drop hotfix suffix (e.g. -h2)
    parts = head.split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def version_tuple(version: str | None) -> tuple[int, int, int, int]:
    """Parse a PAN-OS version into a comparable tuple including the hotfix.

    ``'12.1.4-h6' -> (12, 1, 4, 6)``; ``'12.1.2' -> (12, 1, 2, 0)``;
    ``'11.2.11' -> (11, 2, 11, 0)``. Non-numeric parts become 0, so the result
    is always a 4-tuple safe to order with ``<``/``>`` — e.g. to tell whether a
    firewall target outranks the Panorama managing it. Empty/None -> all zeros.
    """
    if not version:
        return (0, 0, 0, 0)
    main, _, hot = version.strip().partition("-")
    nums: list[int] = []
    for part in main.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    hot_digits = "".join(ch for ch in hot if ch.isdigit())
    return (nums[0], nums[1], nums[2], int(hot_digits) if hot_digits else 0)


def base_image_from_list(target_version: str, software: list[dict]) -> str | None:
    """The BASE image version for ``target_version``'s feature train, read from
    a software list (entries as returned by :meth:`PanDeviceClient.list_software`).

    PAN-OS marks exactly one entry per train with ``release_type == "Base"`` —
    and it is NOT always X.Y.0 (12.1's base image is 12.1.2). We trust that
    marker rather than guessing the ".0". Falls back to the lowest non-hotfix
    maintenance release of the train for older PAN-OS that doesn't populate
    release-type. Returns None if the train isn't present in the list.
    """
    train = feature_train(target_version)
    if train is None:
        return None
    in_train = [e for e in software if feature_train(e.get("version")) == train]
    if not in_train:
        return None
    for e in in_train:
        if (e.get("release_type") or "").strip().lower() == "base":
            return e.get("version")

    # Fallback for PAN-OS that doesn't populate release-type: the lowest-numbered
    # non-hotfix release in the train (the closest analogue to "the base").
    def _ver_key(e: dict) -> tuple:
        head = (e.get("version") or "").split("-", 1)[0]
        try:
            return tuple(int(x) for x in head.split("."))
        except ValueError:
            return (9999,)

    non_hotfix = [e for e in in_train if "-" not in (e.get("version") or "")]
    return min(non_hotfix or in_train, key=_ver_key).get("version")


class PanDeviceClient:
    """Operations against a single firewall — direct or Panorama-proxied."""

    def __init__(self, proxy: FirewallProxy):
        self._proxy = proxy

    # ---------- factories ----------

    @classmethod
    def direct(
        cls,
        host: str,
        api_key: str,
        *,
        verify_tls: bool = True,
        timeout_s: float = DEFAULT_OP_TIMEOUT_S,
    ) -> "PanDeviceClient":
        """Talk straight to the device's mgmt IP/hostname using its API key."""
        # Pass timeout to the CONSTRUCTOR, not after. pan-os-python builds and
        # caches the xapi at construction with its default 1200s timeout, and
        # the urlopen socket timeout (connect + read) is read from that xapi.
        # Setting `.timeout` afterward is a no-op — the cached xapi keeps 1200s,
        # so an unreachable mgmt IP rides the ~127s OS TCP-connect timeout per
        # op() before failing (≈405s for a 3-command capacity poll).
        proxy = FirewallProxy(hostname=host, api_key=api_key, timeout=timeout_s)
        proxy.verify_ssl = verify_tls
        return cls(proxy)

    @classmethod
    def via_panorama(
        cls,
        panorama_host: str,
        panorama_api_key: str,
        target_serial: str,
        *,
        verify_tls: bool = True,
        timeout_s: float = DEFAULT_OP_TIMEOUT_S,
    ) -> "PanDeviceClient":
        """Talk to the device by routing op() calls through Panorama (target=<serial>).

        Build a Panorama with the API key, attach a Firewall(serial=...) child so
        its op() calls flow through Panorama, then wrap that Firewall in a
        FirewallProxy so pan-os-upgrade-assurance can use it.
        """
        # Timeout must be set at construction — the xapi caches it; setting
        # `.timeout` afterward is a no-op (see direct()). Panorama's own xapi is
        # what a proxied op() rides, so the constructor timeout on `pano` is the
        # one that bites when Panorama itself is unreachable.
        pano = PanosPanorama(panorama_host, api_key=panorama_api_key, timeout=timeout_s)
        pano.verify_ssl = verify_tls

        fw = PanosFirewall(serial=target_serial)
        pano.add(fw)
        proxy = FirewallProxy(firewall=fw)
        # FirewallProxy(firewall=...) builds its OWN xapi at the 1200s default;
        # the timeout is read per-request, so force the bound value onto that
        # live xapi too — covers the proxy path regardless of which xapi op()
        # ends up using.
        proxy.xapi.timeout = timeout_s
        return cls(proxy)

    # ---------- low-level op (capacity polling uses this) ----------

    def op_xml(self, xml_cmd: str) -> ET.Element:
        """Run an arbitrary XML op command and return the parsed response.

        Capacity polling needs this for every metric in the catalog (each
        metric is a `(current, max)` pair of XML op commands). Higher-level
        callers (upgrade orchestrator, precheck) use the typed methods
        below instead — `op_xml` is the escape hatch for arbitrary commands
        that don't have a typed wrapper.

        Raises `TargetDisconnectedError` (a `ConnectionError` subclass) when
        the failure is "Panorama replied that the target firewall is
        offline" so callers can avoid mis-attributing it to Panorama health.
        """
        try:
            return self._proxy.op(xml_cmd, cmd_xml=False)
        except PanDeviceError as exc:
            _raise_op_error(exc, f"op() failed for cmd {xml_cmd!r}")

    # ---------- read-only introspection ----------

    def get_system_info(self) -> SystemInfo:
        """One round-trip to populate everything our DB cares about.

        Raises `TargetDisconnectedError` (a `ConnectionError` subclass) when
        the underlying op() failed specifically because Panorama reported
        the target firewall as offline. Lets the proxy-fallback builder
        avoid flagging Panorama itself as unreachable in that case.
        """
        try:
            sys_resp = self._proxy.op("<show><system><info></info></system></show>", cmd_xml=False)
            ha_resp = self._proxy.op(
                "<show><high-availability><state></state></high-availability></show>", cmd_xml=False
            )
        except PanDeviceError as exc:
            _raise_op_error(exc, "Device op() failed")

        info = sys_resp.find(".//system")
        ha_state = _text(ha_resp, ".//local-info/state") or _text(ha_resp, ".//state")
        ha_sync = _text(ha_resp, ".//running-sync") or _text(ha_resp, ".//local-info/state-sync")
        ha_peer = _text(ha_resp, ".//peer-info/serial-num") or _text(ha_resp, ".//peer/serial")
        if ha_state in (None, "", "disabled"):
            ha_state = None

        return SystemInfo(
            hostname=_text(info, "hostname"),
            serial=_text(info, "serial"),
            model=_text(info, "model"),
            sw_version=_text(info, "sw-version"),
            uptime=_text(info, "uptime"),
            app_version=_text(info, "app-version"),
            threat_version=_text(info, "threat-version"),
            av_version=_text(info, "av-version"),
            wildfire_version=_text(info, "wildfire-version"),
            url_filtering_version=_text(info, "url-filtering-version"),
            gp_client_version=_text(info, "global-protect-client-package-version"),
            ha_state=ha_state,
            ha_sync_state=ha_sync,
            ha_peer_serial=ha_peer,
        )

    def get_licenses(self) -> dict | None:
        """Return `request license info` as a JSON-friendly dict (best effort)."""
        try:
            resp = self._proxy.op("<request><license><info></info></license></request>", cmd_xml=False)
        except PanDeviceError as exc:
            log.warning("get_licenses failed: %s", exc)
            return None
        # Convert the XML subtree to a dict keyed by license name where possible.
        licenses: dict = {}
        for entry in resp.findall(".//licenses/entry"):
            name = _text(entry, "feature") or _text(entry, "name") or ""
            if not name:
                continue
            licenses[name] = {
                "description": _text(entry, "description"),
                "serial": _text(entry, "serial"),
                "issued": _text(entry, "issued"),
                "expires": _text(entry, "expires"),
                "expired": (_text(entry, "expired") or "").lower() == "yes",
                "auth_code": _text(entry, "authcode"),
            }
        return licenses or None

    # ---------- pre/post checks via pan-os-upgrade-assurance ----------
    #
    # `skip_force_locale=True` on every CheckFirewall ctor is load-
    # bearing. The library's __init__ unconditionally calls
    # `locale.setlocale(LC_ALL, "en_US.UTF-8")` — used internally for
    # `strptime` of datetime strings from the firewall. Our container
    # has C.UTF-8 (not en_US.UTF-8) installed, so Python raises
    # `locale.Error: unsupported locale setting` and the readiness
    # check call dies before evaluating any check. The library added
    # `skip_force_locale` as the documented opt-out exactly for
    # containerized deployments like ours. Month/weekday strptime
    # patterns work fine under C.UTF-8 because C locale uses English.
    #
    # Symptom if removed: precheck/postcheck runs persist with
    # pass=warn=fail=skip=0 and error="Readiness checks failed:
    # unsupported locale setting" — orchestrator parks the task at
    # AWAITING_PRECHECK_OVERRIDE indefinitely.

    def run_readiness_checks(self, checks: list[str] | None = None) -> dict:
        names = list(checks or DEFAULT_READINESS_CHECKS)
        try:
            results = CheckFirewall(self._proxy, skip_force_locale=True).run_readiness_checks(checks_configuration=names)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # ---- Cloud-managed firewall (Strata Cloud Manager / SCM) ----
            # `show panorama-status` returns a different shape on SCM-managed
            # firewalls and pan-os-upgrade-assurance raises
            # `PanoramaConfigurationMissingException` with this exact wording.
            # That single failure currently kills the entire bulk run.
            #
            # Fix: drop the panorama check and re-run. We synthesize a
            # passing "not applicable" result for `panorama` afterwards so
            # the classifier produces a clean badge instead of a missing one.
            # Two known cases where a single check raising kills the whole
            # bulk run. In both we drop the offending check, retry, and
            # synthesize a stand-in result so the classifier produces a clean
            # row instead of a missing one.
            drop: str | None = None
            synth: dict | None = None

            if "Cloud management is enabled" in msg and "panorama" in names:
                # SCM-managed firewall — see prior commit for the back-story.
                drop = "panorama"
                synth = {
                    "state": True,
                    "reason": "Cloud-managed firewall (Strata Cloud Manager) — Panorama check not applicable",
                }
            elif (
                "Failed to check Content" in msg
                or "content upgrade info" in msg
            ) and "content_version" in names:
                # The library's content_version check asks the firewall to
                # phone home to updates.paloaltonetworks.com. When the
                # firewall has no internet egress (lab, air-gapped, broken
                # update profile), the library re-raises the SSL/connect
                # failure and the whole run dies. The check is genuinely
                # impossible without egress; treat as a warning, not a
                # blocker.
                drop = "content_version"
                synth = {
                    "state": False,
                    "reason": (
                        "Firewall could not reach updates.paloaltonetworks.com "
                        "to compare content version — check the device's "
                        "Update Server reachability / Service Route / DNS. "
                        f"Underlying error: {msg[:200]}"
                    ),
                }

            if drop is not None and synth is not None:
                names = [n for n in names if n != drop]
                try:
                    results = CheckFirewall(self._proxy, skip_force_locale=True).run_readiness_checks(checks_configuration=names)
                except Exception as exc2:  # noqa: BLE001
                    raise ConnectionError(_friendly_check_error(exc2)) from exc2
                results[drop] = synth
            else:
                raise ConnectionError(_friendly_check_error(exc)) from exc

        normalized: dict[str, dict] = {}
        for name, res in results.items():
            if isinstance(res, dict):
                normalized[name] = {
                    "state": bool(res.get("state")),
                    "reason": str(res.get("reason", "")),
                }
            else:
                state = getattr(res, "state", None)
                reason = getattr(res, "reason", "") or ""
                state_bool = (
                    bool(state)
                    if isinstance(state, bool)
                    else str(state).lower().endswith("success")
                )
                normalized[name] = {"state": state_bool, "reason": str(reason)}
        return normalized

    def take_snapshot(self, snapshots: list[str] | None = None) -> dict:
        return CheckFirewall(self._proxy, skip_force_locale=True).run_snapshots(
            snapshots_config=list(snapshots) if snapshots else None
        )

    # ---------- software (download / staging) ----------

    def list_software(self) -> list[dict]:
        """`request system software info` — return all known versions.

        Each entry: {version, downloaded(bool), current(bool), latest(bool),
                     uploaded(bool), filename, released_on, size_kb,
                     release_type}. ``release_type == "Base"`` flags the single
                     base image per feature train (consumed by
                     ``base_image_from_list``); it is NOT always X.Y.0.
        """
        try:
            self._proxy.op("<request><system><software><check></check></software></system></request>", cmd_xml=False)
        except PanDeviceError:
            # `check` reaches out to updates.paloaltonetworks.com; ok to ignore failure here,
            # the next call will still return whatever the device knows about.
            pass
        try:
            resp = self._proxy.op("<request><system><software><info></info></software></system></request>", cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"software info failed: {exc}") from exc

        out: list[dict] = []
        for entry in resp.findall(".//sw-updates/versions/entry") or resp.findall(".//entry"):
            out.append({
                "version": _text(entry, "version"),
                "downloaded": (_text(entry, "downloaded") or "").lower() == "yes",
                "current": (_text(entry, "current") or "").lower() == "yes",
                "latest": (_text(entry, "latest") or "").lower() == "yes",
                "uploaded": (_text(entry, "uploaded") or "").lower() == "yes",
                "filename": _text(entry, "filename"),
                "released_on": _text(entry, "released-on"),
                "size_kb": _text(entry, "size-kb") or _text(entry, "size"),
                "release_type": (_text(entry, "release-type") or "").strip(),
            })
        return out

    def is_version_downloaded(self, version: str) -> bool:
        for entry in self.list_software():
            if entry["version"] == version and (entry["downloaded"] or entry["current"]):
                return True
        return False

    def request_software_download(self, version: str) -> str:
        """Kick off `request system software download version X.Y.Z`. Returns the job id."""
        cmd = f"<request><system><software><download><version>{version}</version></download></software></system></request>"
        try:
            resp = self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"software download request failed: {exc}") from exc
        return _text(resp, ".//job") or ""

    def get_job_status(self, job_id: str) -> dict:
        """Poll a software-download (or other) job by id."""
        cmd = f"<show><jobs><id>{job_id}</id></jobs></show>"
        try:
            resp = self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"job status failed: {exc}") from exc
        job = resp.find(".//job")
        if job is None:
            return {"status": "unknown", "progress": "0", "result": "unknown", "details": ""}
        # Multiple <line> elements under <details> — keep them all so the user
        # sees the whole story when something goes wrong.
        detail_lines = [
            (line.text or "").strip() for line in job.findall("details/line") if line.text
        ]
        return {
            "status": _text(job, "status") or "unknown",     # "ACT", "FIN", "PEND"
            "progress": _text(job, "progress") or "0",
            "result": _text(job, "result") or "unknown",     # "OK" / "FAIL"
            "details": "\n".join(detail_lines),
        }

    def import_software_image(self, local_path: str) -> None:
        # TODO: SCP-style import for uploaded images
        raise NotImplementedError

    # ---------- disk space self-help ----------

    def get_disk_space(self) -> list[dict]:
        """Parse `show system disk-space` into structured rows.

        PAN-OS returns the output of unix `df -h` inside <result>, headers
        included. We parse it back into a list of dicts so the UI doesn't
        have to render fixed-width text. Returns:
          [{"filesystem", "size", "used", "avail", "use_pct", "mounted_on"}, ...]

        Empty list on parse failure — caller is responsible for treating
        that as "we couldn't tell, don't act on it."
        """
        try:
            resp = self._proxy.op(
                "<show><system><disk-space></disk-space></system></show>",
                cmd_xml=False,
            )
        except PanDeviceError as exc:
            raise ConnectionError(f"disk-space query failed: {exc}") from exc
        text = (resp.find(".//result").text if resp.find(".//result") is not None else None) or ""
        rows: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("filesystem"):
                continue
            # df output: Filesystem Size Used Avail Use% Mounted-on
            parts = line.split()
            if len(parts) < 6:
                continue
            rows.append({
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "use_pct": parts[4].rstrip("%"),
                "mounted_on": " ".join(parts[5:]),
            })
        return rows

    def delete_software_image(self, version: str) -> None:
        """`request system software delete version X.Y.Z`.

        Frees disk by removing a previously-downloaded image. Refuses on the
        device side if you try to delete the running version, so this is
        safe to call without local guarding (we guard anyway in the caller).
        """
        cmd = (
            f"<request><system><software><delete><version>{version}</version>"
            f"</delete></software></system></request>"
        )
        try:
            self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"software delete failed: {exc}") from exc

    def disk_usage_cleanup(self) -> str:
        """`debug software disk-usage cleanup` — reclaim stale install /
        download artifacts PAN-OS knows how to clear on its own.

        STANDARD form only. We deliberately do NOT run the ``deep`` /
        ``aggressive-cleaning`` variants — those delete current log files and
        cost troubleshooting history. Returns the device's textual result
        (best-effort; empty string when the device returns nothing). Callers
        treat a failure as non-fatal: some PAN-OS builds may not accept the
        bare ``cleanup`` form, but the old-image deletion is the primary win.
        """
        cmd = "<debug><software><disk-usage><cleanup></cleanup></disk-usage></software></debug>"
        try:
            resp = self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"disk-usage cleanup failed: {exc}") from exc
        res = resp.find(".//result")
        return (res.text or "").strip() if res is not None and res.text else ""

    def request_software_install(self, version: str) -> str:
        """`request system software install version X.Y.Z` — returns the job id.

        The device installs and reboots; this command itself returns quickly with
        a job we can poll. After job completion the device is in the middle of
        rebooting, so subsequent ops will fail until wait_for_ready() succeeds.
        """
        cmd = (
            f"<request><system><software><install><version>{version}</version>"
            f"</install></software></system></request>"
        )
        try:
            resp = self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"software install request failed: {exc}") from exc
        return _text(resp, ".//job") or ""

    def suspend_ha(self) -> None:
        """Take this HA member out of normal HA so it can be upgraded alone."""
        cmd = "<request><high-availability><state><suspend></suspend></state></high-availability></request>"
        try:
            self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"HA suspend failed: {exc}") from exc

    def resume_ha(self) -> None:
        """Bring this HA member back to functional state after an upgrade."""
        cmd = "<request><high-availability><state><functional></functional></state></high-availability></request>"
        try:
            self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"HA resume failed: {exc}") from exc

    def trigger_failover(self) -> None:
        """Run on the ACTIVE member to hand off to the peer.

        PAN-OS pattern: suspend the active member; the peer takes over.
        NOTE: callers must verify peer health BEFORE calling this — suspending
        the active without a healthy peer is an outage.
        """
        self.suspend_ha()

    def restart_system(self) -> None:
        """`request restart system` — reboots the device.

        `request system software install` does not reboot on its own; install
        the new version, then call this. The op typically returns OK
        immediately and the device starts rebooting; use wait_for_ready() to
        poll until the mgmt plane is back.
        """
        cmd = "<request><restart><system></system></restart></request>"
        try:
            self._proxy.op(cmd, cmd_xml=False)
        except PanDeviceError as exc:
            # Some PAN-OS versions terminate the API session mid-restart and
            # raise here even though the reboot is in progress. Treat as soft;
            # the caller will wait_for_ready and find out the truth.
            log.warning("restart_system op raised (often expected at reboot): %s", exc)

    def wait_for_ready(
        self,
        timeout_s: int = 1800,
        poll_interval_s: int = 30,
        consecutive_oks: int = 3,
    ) -> None:
        """Poll until the device's mgmt plane is responsive again post-reboot.

        Requires `consecutive_oks` successful probes in a row (with a poll
        interval between each) before declaring the device ready — guards
        against the common boot-time pattern where the API briefly responds
        and then goes back down as other subsystems initialize. Without
        this, downstream HA control ops fail with Connection-refused even
        though wait_for_ready "succeeded."

        Raises TimeoutError if it doesn't come back in `timeout_s`.
        """
        import time as _t

        deadline = _t.monotonic() + timeout_s
        last_exc: Exception | None = None
        ok_streak = 0
        while _t.monotonic() < deadline:
            try:
                self.get_system_info()
                ok_streak += 1
                if ok_streak >= consecutive_oks:
                    return
                # Got an OK but haven't met the threshold — wait then probe again.
                _t.sleep(poll_interval_s)
            except Exception as exc:  # noqa: BLE001
                # Any failure resets the streak — the device must be
                # consistently up, not flapping.
                ok_streak = 0
                last_exc = exc
                _t.sleep(poll_interval_s)
        raise TimeoutError(
            f"Device did not stay up (need {consecutive_oks} consecutive successes) "
            f"in {timeout_s}s; last error: {last_exc}"
        )


def keygen(hostname: str, username: str, password: str, *, verify_tls: bool = True) -> str:
    """Exchange username+password for a long-lived API key (works for fw or Panorama).

    Wraps the underlying httpx call in `_friendly_check_error` so the
    operator sees an actionable hint ("DNS lookup failed for the device
    hostname...") instead of the raw OS / library exception
    ("[Errno -2] Name or service not known"). The context kwarg
    differentiates this caller in the friendly message — the operator
    knows the failure was during initial auth setup, not during
    ongoing polling.

    See pan-ops-console#46 — operator surfaced the DNS error as
    "not entirely useful or friendly for the average user."
    """
    import httpx

    url = f"https://{hostname}/api/"
    params = {"type": "keygen", "user": username, "password": password}
    try:
        with httpx.Client(verify=verify_tls, timeout=15.0) as client:
            resp = client.get(url, params=params)
        resp.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        raise ValueError(_friendly_check_error(exc, context="Authentication")) from exc
    root = ET.fromstring(resp.text)
    if root.get("status") != "success":
        msg = root.findtext(".//msg") or resp.text
        # PAN-OS responded but rejected the request. _friendly_check_error
        # handles the common shapes (Invalid Credential, 401, etc.) — fall
        # through so "Authentication: ..." prefixed message wraps it.
        raise ValueError(
            _friendly_check_error(Exception(msg), context="Authentication")
        )
    key = root.findtext(".//key")
    if not key:
        raise ValueError("keygen succeeded but response had no <key>")
    return key
