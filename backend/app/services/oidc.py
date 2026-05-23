"""OIDC client.

Provider-agnostic — wires any standards-compliant OpenID Connect IdP
(Authentik, Keycloak, Google, Okta, Entra ID, GitHub-via-OAuth-compat,
etc.) into the app's session auth.

Configuration is env-driven via the OIDC_PROVIDER_<NAME>_* pattern;
see app.config for the schema. Providers are loaded once at module
import and exposed by name.

State + PKCE flow:
- /auth/oidc/<name>/login generates a random `state` and PKCE pair,
  stashes them in an in-memory dict keyed by state, and 302-redirects
  to the IdP authorization endpoint.
- /auth/oidc/<name>/callback receives ?code=&state=, validates state,
  exchanges code+verifier for tokens at the IdP, validates the ID
  token signature against the IdP's JWKS, extracts the claims, and
  resolves/creates the local user. On success, creates a session and
  redirects to "/".

In-memory state is fine for the single-replica deploy this app is
designed for. A multi-replica deploy would need a shared store
(Redis or a `oidc_state` table). Documented in the docstring of
`_pending_states` below so the next person knows where to look.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from authlib.integrations.httpx_client import OAuth2Client
from authlib.jose import JsonWebKey, jwt

log = logging.getLogger(__name__)


@dataclass
class OIDCProvider:
    """A single configured OIDC provider."""

    name: str                       # short slug used in URLs (e.g. "authentik")
    display_name: str               # rendered on the login page button
    issuer: str                     # IdP issuer URL (no trailing slash)
    client_id: str
    client_secret: str
    scopes: list[str]               # at least ["openid"]; usually + email + profile


# Module-level cache: name -> provider. Empty if no env vars set.
_PROVIDERS: dict[str, OIDCProvider] = {}

# Pending OAuth states. Keyed by random state token; value = {
#   "provider": name,
#   "code_verifier": str,
#   "nonce": str,
#   "created_at": float (epoch),
# }
#
# In-memory is fine for single-replica. For multi-replica, swap this
# for a small DB table with TTL cleanup.
_pending_states: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600  # 10 minutes from /login to /callback


def load_providers() -> dict[str, OIDCProvider]:
    """Scan os.environ for OIDC_PROVIDER_<NAME>_* vars and build the
    provider table. Re-callable; replaces the cache.
    """
    found: dict[str, OIDCProvider] = {}
    # First pass: collect names by looking for *_ISSUER keys.
    for k in list(os.environ):
        if not k.startswith("OIDC_PROVIDER_") or not k.endswith("_ISSUER"):
            continue
        name = k[len("OIDC_PROVIDER_"): -len("_ISSUER")]
        if not name:
            continue

        def _g(suffix: str, default: str = "") -> str:
            return os.environ.get(f"OIDC_PROVIDER_{name}_{suffix}", default).strip()

        issuer = _g("ISSUER")
        client_id = _g("CLIENT_ID")
        client_secret = _g("CLIENT_SECRET")
        if not (issuer and client_id and client_secret):
            log.warning("OIDC provider %s missing required env vars; skipping", name)
            continue

        display_name = _g("DISPLAY_NAME") or name.title()
        scopes_raw = _g("SCOPES") or "openid email profile"
        scopes = [s.strip() for s in scopes_raw.split() if s.strip()]
        if "openid" not in scopes:
            scopes.insert(0, "openid")

        # Lowercase the slug for URL safety, preserve original mixed case
        # for the display_name only.
        slug = name.lower()
        found[slug] = OIDCProvider(
            name=slug,
            display_name=display_name,
            issuer=issuer.rstrip("/"),
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        log.info("loaded OIDC provider %s (issuer=%s)", slug, issuer)

    _PROVIDERS.clear()
    _PROVIDERS.update(found)
    return _PROVIDERS


def get_provider(name: str) -> OIDCProvider | None:
    return _PROVIDERS.get(name.lower())


def list_provider_names() -> list[str]:
    """Slugs for the bootstrap-status response — UI renders one button per."""
    return sorted(_PROVIDERS.keys())


# ---------- Discovery / metadata ----------

# Cache discovery responses for an hour so we're not hitting the IdP on
# every login. IdPs publish .well-known/openid-configuration which lists
# their authorization, token, jwks, userinfo endpoints.
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_DISCOVERY_TTL = 3600.0


def discover(provider: OIDCProvider) -> dict[str, Any]:
    """Fetch (or return cached) IdP metadata."""
    import httpx

    cached = _discovery_cache.get(provider.issuer)
    if cached and time.time() - cached[0] < _DISCOVERY_TTL:
        return cached[1]

    url = f"{provider.issuer}/.well-known/openid-configuration"
    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        r.raise_for_status()
        meta = r.json()
    _discovery_cache[provider.issuer] = (time.time(), meta)
    return meta


# ---------- Code flow ----------

def begin_login(provider: OIDCProvider, redirect_uri: str) -> str:
    """Build the IdP authorization URL and stash pending state. Returns
    the URL the user agent should be redirected to."""
    meta = discover(provider)
    auth_url = meta["authorization_endpoint"]

    # PKCE — challenge sent now, verifier kept until callback
    code_verifier = secrets.token_urlsafe(64)
    from hashlib import sha256
    import base64
    code_challenge = base64.urlsafe_b64encode(
        sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    _gc_pending_states()
    _pending_states[state] = {
        "provider": provider.name,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }

    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    import urllib.parse
    qs = urllib.parse.urlencode(params)
    sep = "&" if "?" in auth_url else "?"
    return f"{auth_url}{sep}{qs}"


def complete_login(state: str, code: str) -> dict[str, Any]:
    """Validate state, exchange the code for tokens, validate the ID token,
    and return the parsed claims. Raises ValueError on any failure.
    """
    pending = _pending_states.pop(state, None)
    if pending is None:
        raise ValueError("unknown or expired state")
    if time.time() - pending["created_at"] > _STATE_TTL_SECONDS:
        raise ValueError("state expired")

    provider = get_provider(pending["provider"])
    if provider is None:
        raise ValueError(f"provider {pending['provider']} no longer configured")

    meta = discover(provider)

    # Exchange code → tokens
    client = OAuth2Client(
        client_id=provider.client_id,
        client_secret=provider.client_secret,
        token_endpoint_auth_method="client_secret_post",
    )
    tokens = client.fetch_token(
        url=meta["token_endpoint"],
        grant_type="authorization_code",
        code=code,
        redirect_uri=pending["redirect_uri"],
        code_verifier=pending["code_verifier"],
    )

    id_token_raw = tokens.get("id_token")
    if not id_token_raw:
        raise ValueError("IdP did not return an id_token")

    # Fetch JWKS and validate the ID token signature
    import httpx
    with httpx.Client(timeout=10.0) as h:
        jwks_doc = h.get(meta["jwks_uri"]).json()
    keys = JsonWebKey.import_key_set(jwks_doc)
    claims = jwt.decode(
        id_token_raw,
        keys,
        claims_options={
            "iss": {"essential": True, "values": [provider.issuer]},
            "aud": {"essential": True, "values": [provider.client_id]},
            "nonce": {"essential": True, "values": [pending["nonce"]]},
        },
    )
    claims.validate()
    return dict(claims)


def _gc_pending_states() -> None:
    now = time.time()
    expired = [
        s for s, v in _pending_states.items()
        if now - v["created_at"] > _STATE_TTL_SECONDS
    ]
    for s in expired:
        _pending_states.pop(s, None)


# Load providers at import time so the routes can reference them.
load_providers()
