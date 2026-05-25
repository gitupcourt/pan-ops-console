"""Connectivity diagnostic for a Device — exactly the things a 'why can't
my container reach this firewall' debug session needs to know.

Four independent steps, each reported as pass/fail with its own latency
and message. They run sequentially because a later step can't be
meaningful if an earlier step failed (no point checking TLS if TCP/443
won't open). When a step fails, subsequent steps return "skipped".

  1. DNS — does the hostname resolve at all? What does it resolve to?
     This is the #1 silent gotcha when Docker Desktop's WSL2 DNS isn't
     honoring the host's resolver config; the container ends up
     resolving the firewall to nothing, or to a loopback, and you get
     connection-refused on a host that "works fine in my browser."
  2. TCP — can we open a socket to <ip>:443? A successful open here +
     a failing browser would be suspicious; a failing open here when
     the browser works tells us the container's network namespace is
     the problem (different source IP, blocked egress, etc).
  3. TLS — does the device complete a TLS handshake? Catches
     self-signed-cert issues separately from bad-network issues.
  4. API — does PAN-OS accept the credential and return an API key?
     Catches the case where everything below is fine but the user's
     credential is wrong, or the account doesn't have XML API access.

We deliberately don't fold these into one "yes/no" so the operator gets
exact targeting for fixes.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.models.device import Device
from app.services.credentials import ResolvedCredential, resolve as resolve_credential


# Known Docker-internal subnets. If a firewall's mgmt IP lands here, the
# container can never reach it — Docker captures the packet before it
# leaves the host. This is the actual root cause for a common bug:
# any firewall with a mgmt IP in 192.168.65.0/24 (Docker Desktop's
# vpnkit) gets RST-in-<10ms from a container while the host itself can
# reach the same IP fine, because vpnkit short-circuits the packet.
#
# We flag matches as a HUGE warning on the DNS step so the operator
# doesn't waste time inspecting firewall configs that aren't the problem.
DOCKER_COLLISION_SUBNETS: list[tuple[str, str]] = [
    ("192.168.65.0/24", "Docker Desktop vpnkit (Windows/Mac)"),
    ("172.17.0.0/16", "default Docker bridge"),
    ("172.18.0.0/16", "Docker user-defined bridge (often Compose default)"),
    ("172.19.0.0/16", "Docker user-defined bridge"),
    ("172.20.0.0/16", "Docker user-defined bridge"),
    ("172.21.0.0/16", "Docker user-defined bridge"),
    ("172.22.0.0/16", "Docker user-defined bridge"),
    ("172.23.0.0/16", "Docker user-defined bridge"),
    ("172.24.0.0/16", "Docker user-defined bridge"),
    ("172.25.0.0/16", "Docker user-defined bridge"),
    ("172.26.0.0/16", "Docker user-defined bridge"),
    ("172.27.0.0/16", "Docker user-defined bridge"),
    ("172.28.0.0/16", "Docker user-defined bridge"),
    ("172.29.0.0/16", "Docker user-defined bridge"),
    ("172.30.0.0/16", "Docker user-defined bridge"),
    ("172.31.0.0/16", "Docker user-defined bridge"),
]


def _check_docker_collision(ip_str: str) -> str | None:
    """If the IP lies in a known Docker-internal subnet, return a human
    hint string. None when there's no collision."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    for cidr, label in DOCKER_COLLISION_SUBNETS:
        if ip in ipaddress.ip_network(cidr):
            return (
                f"⚠ {ip_str} lies inside {cidr} which Docker uses for "
                f"{label}. Docker captures traffic to that subnet before it "
                f"leaves the host — the container can NEVER reach this IP, "
                f"even when your desktop can. Either change the firewall's "
                f"mgmt IP to be outside this subnet, or reconfigure Docker's "
                f"network ranges."
            )
    return None


def _read_resolver_state() -> dict[str, str]:
    """Return a small dump of the container's DNS plumbing.

    Useful when the operator reports "I changed the firewall's IP but the
    container is still resolving the old one." Three places to check:
      - /etc/resolv.conf — what nameservers the container uses (Docker
        Desktop substitutes its own embedded resolver here, usually
        127.0.0.11 inside Docker networks).
      - /etc/hosts — any baked-in overrides that bypass DNS entirely.
      - the actual DNS answer — what the configured nameservers return
        right now. Diverging from `getent hosts` would mean glibc has
        cached, but glibc doesn't normally cache so this is just a
        cross-check.
    Each is best-effort; on read failure we skip it rather than failing
    the whole panel.
    """
    out: dict[str, str] = {}
    for path, key in (("/etc/resolv.conf", "resolv_conf"), ("/etc/hosts", "hosts")):
        try:
            with open(path) as f:
                # Trim noise: drop comment-only lines, cap to ~10 entries each.
                lines = [
                    ln.rstrip()
                    for ln in f.readlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ][:10]
                out[key] = "\n".join(lines)
        except OSError:
            pass
    return out


def _route_lookup(target_ip: str) -> str | None:
    """Best-effort: ask the kernel which interface + gateway it would use
    to reach `target_ip` from this container. Returns a one-line summary
    or None if we can't determine it (e.g. iproute2 not installed in the
    image). Pure diagnostic — never affects test pass/fail."""
    try:
        out = subprocess.run(
            ["ip", "-o", "route", "get", target_ip],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            # Output like: "1.2.3.4 via 172.18.0.1 dev eth0 src 172.18.0.6 uid 0"
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


# Default TCP / TLS connect timeout. Short — a happy device replies in
# under a second; if we're at 5s and waiting, it's already broken.
DEFAULT_TIMEOUT_S = 5.0


@dataclass
class StepResult:
    name: str
    status: str          # "pass" | "fail" | "skip"
    message: str
    latency_ms: int | None = None
    detail: str | None = None  # extra info, e.g. resolved IP or cert subject


def run_connectivity_test(device: Device) -> list[dict]:
    """Run the four-step diagnostic. Returns a list of step dicts ready for JSON.

    Pure function over a Device — does not touch the DB. The caller is
    responsible for resolving the device's credential and passing it in
    (or we resolve from the relationship if not provided)."""
    steps: list[StepResult] = []

    host = (device.ip_address or device.hostname or "").strip()
    if not host:
        steps.append(StepResult(
            "host", "fail",
            "Device has no ip_address or hostname set — nothing to test.",
        ))
        return [vars(s) for s in steps]

    # ---- Step 1: DNS ----
    resolved_ip: str | None = None
    t0 = time.monotonic()
    try:
        # Pass 443 so getaddrinfo returns a TCP-relevant result; AI_ADDRCONFIG
        # avoids IPv6 noise when the container only has IPv4.
        infos = socket.getaddrinfo(
            host, 443, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG
        )
        resolved_ip = infos[0][4][0] if infos else None
        dns_latency = int((time.monotonic() - t0) * 1000)
        # Two pieces of follow-on context that make this step actually useful
        # for the Docker-Desktop-subnet-collision case:
        #   - collision detection against known docker-internal subnets
        #   - the kernel's chosen route for the target IP (which interface,
        #     which gateway). If a container is routing the target through
        #     its own bridge gateway, the operator sees that here.
        collision_hint = _check_docker_collision(resolved_ip) if resolved_ip else None
        route_line = _route_lookup(resolved_ip) if resolved_ip else None
        msg_parts = [f"Resolved {host} → {resolved_ip}"]
        if route_line:
            msg_parts.append(f"Route: {route_line}")
        if collision_hint:
            msg_parts.append(collision_hint)
        # When the IP looks unexpected (or the operator just wants to verify
        # what the container is actually seeing), the resolver state below
        # tells the rest of the story: which nameserver answered, and
        # whether there's an /etc/hosts entry overriding things.
        resolver = _read_resolver_state()
        if resolver.get("resolv_conf"):
            msg_parts.append("/etc/resolv.conf:\n" + resolver["resolv_conf"])
        if resolver.get("hosts"):
            msg_parts.append("/etc/hosts:\n" + resolver["hosts"])
        steps.append(StepResult(
            "dns",
            # Collision is not a hard DNS failure — the lookup itself worked —
            # but it's the single most likely reason TCP will fail next, so
            # we surface it as a fail to draw the eye.
            "fail" if collision_hint else "pass",
            "\n".join(msg_parts),
            dns_latency,
            detail=resolved_ip,
        ))
    except socket.gaierror as exc:
        steps.append(StepResult(
            "dns", "fail",
            f"DNS lookup failed: {exc}. "
            f"From a working browser on the same machine the firewall is "
            f"reachable, but the worker container can't resolve the name. "
            f"Try setting the device's mgmt IP directly instead of the FQDN.",
            int((time.monotonic() - t0) * 1000),
        ))
        # Subsequent steps can still attempt with the literal hostname in
        # case it's an /etc/hosts case — but mark them skipped if DNS truly
        # failed.
        for n in ("tcp", "tls", "api"):
            steps.append(StepResult(n, "skip", "DNS step failed — skipping."))
        return [vars(s) for s in steps]
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult(
            "dns", "fail", f"Unexpected DNS error: {exc}",
            int((time.monotonic() - t0) * 1000),
        ))
        for n in ("tcp", "tls", "api"):
            steps.append(StepResult(n, "skip", "DNS step failed — skipping."))
        return [vars(s) for s in steps]

    target_ip = resolved_ip or host

    # ---- Step 2: TCP/443 connect ----
    t0 = time.monotonic()
    try:
        with socket.create_connection((target_ip, 443), timeout=DEFAULT_TIMEOUT_S):
            pass
        steps.append(StepResult(
            "tcp", "pass",
            f"TCP/443 open at {target_ip}",
            int((time.monotonic() - t0) * 1000),
        ))
    except ConnectionRefusedError:
        steps.append(StepResult(
            "tcp", "fail",
            f"TCP/443 actively refused by {target_ip}. The host is up and "
            f"reachable but nothing is listening on port 443 — or a firewall "
            f"between the worker container and the device is sending RST. "
            f"If the device's UI works from your desktop, the worker container "
            f"sees a different source IP (it's NAT'd by Docker) — check the "
            f"device's management profile for permitted-IPs that may exclude "
            f"the Docker host's IP.",
            int((time.monotonic() - t0) * 1000),
        ))
        for n in ("tls", "api"):
            steps.append(StepResult(n, "skip", "TCP step failed — skipping."))
        return [vars(s) for s in steps]
    except socket.timeout:
        steps.append(StepResult(
            "tcp", "fail",
            f"TCP/443 timed out connecting to {target_ip}. "
            f"Packets are being dropped silently somewhere on the path "
            f"(network ACL, host firewall, wrong VLAN). A refused error "
            f"would tell us something is *there*; a timeout means nothing "
            f"is replying at all.",
            int((time.monotonic() - t0) * 1000),
        ))
        for n in ("tls", "api"):
            steps.append(StepResult(n, "skip", "TCP step failed — skipping."))
        return [vars(s) for s in steps]
    except OSError as exc:
        steps.append(StepResult(
            "tcp", "fail",
            f"TCP connect to {target_ip}:443 failed: {exc}",
            int((time.monotonic() - t0) * 1000),
        ))
        for n in ("tls", "api"):
            steps.append(StepResult(n, "skip", "TCP step failed — skipping."))
        return [vars(s) for s in steps]

    # ---- Step 3: TLS handshake ----
    t0 = time.monotonic()
    tls_ctx = ssl.create_default_context()
    if not device.verify_tls:
        # Match the runtime behavior: when verify_tls=false we'd accept
        # self-signed certs. We still attempt the handshake either way so
        # the operator sees if it succeeds — just don't fail on cert issues.
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((target_ip, 443), timeout=DEFAULT_TIMEOUT_S) as sock:
            with tls_ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert_subject = "unverified" if not device.verify_tls else _subject(ssock.getpeercert())
                steps.append(StepResult(
                    "tls", "pass",
                    f"TLS handshake OK ({ssock.version()}, cert subject: {cert_subject})",
                    int((time.monotonic() - t0) * 1000),
                ))
    except ssl.SSLCertVerificationError as exc:
        steps.append(StepResult(
            "tls", "fail",
            f"TLS cert verification failed: {exc}. Either install a trusted "
            f"cert on the firewall, or set verify_tls=false on the device.",
            int((time.monotonic() - t0) * 1000),
        ))
        steps.append(StepResult("api", "skip", "TLS step failed — skipping."))
        return [vars(s) for s in steps]
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult(
            "tls", "fail",
            f"TLS handshake failed: {exc}",
            int((time.monotonic() - t0) * 1000),
        ))
        steps.append(StepResult("api", "skip", "TLS step failed — skipping."))
        return [vars(s) for s in steps]

    # ---- Step 4: API keygen ----
    if device.credential is None:
        steps.append(StepResult(
            "api", "skip",
            "Device has no credential attached — skipping API check.",
        ))
        return [vars(s) for s in steps]

    cred = resolve_credential(device.credential)
    t0 = time.monotonic()
    try:
        api_key, info = _try_api_keygen(host, cred, verify_tls=device.verify_tls)
        steps.append(StepResult(
            "api", "pass" if api_key else "fail",
            "Credential accepted; API key issued."
            if api_key
            else f"PAN-OS responded but didn't return an API key: {info}",
            int((time.monotonic() - t0) * 1000),
        ))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500] if hasattr(exc, "read") else ""
        steps.append(StepResult(
            "api", "fail",
            f"PAN-OS rejected the request with HTTP {exc.code}: {body or exc.reason}. "
            f"401/403 → wrong credential or no XML API access for that user.",
            int((time.monotonic() - t0) * 1000),
        ))
    except urllib.error.URLError as exc:
        steps.append(StepResult(
            "api", "fail",
            f"API call failed at the network layer: {exc.reason}. "
            f"(TCP+TLS succeeded above, so this is likely a partial-firewall "
            f"or HTTP-vs-HTTPS mismatch — rare.)",
            int((time.monotonic() - t0) * 1000),
        ))
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult(
            "api", "fail",
            f"Unexpected error during API call: {exc}",
            int((time.monotonic() - t0) * 1000),
        ))

    return [vars(s) for s in steps]


def _subject(cert: dict | None) -> str:
    if not cert:
        return "(none)"
    subj = cert.get("subject") or ()
    # subject is a tuple of tuples like ((('commonName', 'fw-01'),),)
    for rdn in subj:
        for k, v in rdn:
            if k == "commonName":
                return v
    return "(no CN)"


def _try_api_keygen(
    host: str, cred: ResolvedCredential, *, verify_tls: bool
) -> tuple[str | None, str]:
    """Hit `/api/?type=keygen` directly. Returns (api_key, info_string)."""
    # If the credential is already an api_key, run `?type=op&cmd=<show><system><info>...`
    # instead — that's the simplest "does this key work" probe.
    if cred.api_key:
        params = urllib.parse.urlencode({
            "type": "op",
            "cmd": "<show><system><info></info></system></show>",
            "key": cred.api_key,
        })
    else:
        params = urllib.parse.urlencode({
            "type": "keygen",
            "user": cred.username or "",
            "password": cred.password or "",
        })
    url = f"https://{host}/api/?{params}"

    ssl_ctx = ssl.create_default_context()
    if not verify_tls:
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S, context=ssl_ctx) as resp:
        body = resp.read().decode(errors="replace")
        if cred.api_key:
            # Op call — success means we got a response at all.
            return ("(api_key)", body[:300])
        # keygen returns <response><result><key>...</key></result></response>
        if "<key>" in body and "</key>" in body:
            key = body.split("<key>", 1)[1].split("</key>", 1)[0]
            return (key, "")
        return (None, body[:300])
