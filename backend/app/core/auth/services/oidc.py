"""OIDC client.

Provider-agnostic — wires any standards-compliant OpenID Connect IdP
(Authentik, Keycloak, Entra ID, Google, Okta, …) into the app's
session auth.

Providers are stored as OIDCProvider rows in the DB and managed through
the admin UI (/providers/oidc routes). Env-var providers from a previous
generation of this app continue to work as a fallback during initial
deploy, but rows in the DB always take precedence on slug conflict.

State + PKCE flow:
- /auth/oidc/<slug>/login generates a random `state` and PKCE pair,
  stashes them in an in-memory dict keyed by state, and 302-redirects
  to the IdP authorization endpoint.
- /auth/oidc/<slug>/callback receives ?code=&state=, validates state,
  exchanges code+verifier for tokens at the IdP, validates the ID
  token signature against the IdP's JWKS, extracts the claims, and
  resolves/creates the local user. On success, creates a session and
  redirects to "/".

In-memory state + discovery cache are fine for the single-replica
deploy this app is designed for. A multi-replica deploy would need a
shared store (Redis or a `oidc_state` table). Documented in the
docstring of `_pending_states` below so the next person knows where
to look.
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
from sqlalchemy.orm import Session as DBSession

from app.core.credentials import decrypt_key, encrypt_key

log = logging.getLogger(__name__)


@dataclass
class OIDCProviderConfig:
    """A loaded provider, ready for use. Source is either DB or env."""
    slug: str
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str]


# Pending OAuth states. Keyed by random state token; value = {
#   "provider": slug, "code_verifier": str, "nonce": str,
#   "redirect_uri": str, "created_at": float (epoch),
# }
#
# In-memory is fine for single-replica. For multi-replica, swap this
# for a small DB table with TTL cleanup.
_pending_states: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600  # 10 minutes from /login to /callback

# Discovery (well-known) cache. Keyed by issuer URL.
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_DISCOVERY_TTL = 3600.0


def list_enabled_providers(db: DBSession) -> list[OIDCProviderConfig]:
    """All enabled providers from the DB, merged with env-var fallbacks
    that don't conflict by slug. DB always wins on conflict."""
    from app.core.auth.models.oidc_provider import OIDCProvider

    out: dict[str, OIDCProviderConfig] = {}

    # Env-var fallback first; DB overrides.
    for p in _load_env_providers().values():
        out[p.slug] = p

    rows = db.query(OIDCProvider).filter(OIDCProvider.enabled == True).all()  # noqa: E712
    for row in rows:
        try:
            secret = decrypt_key(row.encrypted_client_secret, purpose=f"oidc:{row.slug}")
        except Exception as exc:
            log.warning("OIDC provider %s: client secret decrypt failed (%s); skipping",
                        row.slug, type(exc).__name__)
            continue
        scopes = [s for s in (row.scopes or "openid email profile").split() if s]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        out[row.slug] = OIDCProviderConfig(
            slug=row.slug,
            display_name=row.display_name,
            issuer=row.issuer.rstrip("/"),
            client_id=row.client_id,
            client_secret=secret,
            scopes=scopes,
        )
    return list(out.values())


def get_provider(db: DBSession, slug: str) -> OIDCProviderConfig | None:
    slug = slug.lower()
    for p in list_enabled_providers(db):
        if p.slug == slug:
            return p
    return None


def list_provider_names(db: DBSession) -> list[str]:
    """Slugs surfaced in /auth/bootstrap-status. UI renders one button per."""
    return sorted(p.slug for p in list_enabled_providers(db))


def invalidate_discovery_cache(issuer: str | None = None) -> None:
    """Drop cached well-known docs. Call after a provider create/update
    so the next login uses the fresh issuer URL."""
    if issuer is None:
        _discovery_cache.clear()
    else:
        _discovery_cache.pop(issuer.rstrip("/"), None)


# ---------- Helpers for the admin routes ----------

def encrypt_client_secret(plaintext: str) -> bytes:
    return encrypt_key(plaintext)


# ---------- Env-var fallback (legacy / bootstrap) ----------

def _load_env_providers() -> dict[str, OIDCProviderConfig]:
    """Scan OIDC_PROVIDER_<NAME>_* env vars and build configs.

    Kept for backward compatibility and so an operator can pre-seed a
    provider before any admin exists (rare, but possible). DB rows
    always take precedence — see list_enabled_providers.
    """
    found: dict[str, OIDCProviderConfig] = {}
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
            continue
        display_name = _g("DISPLAY_NAME") or name.title()
        scopes_raw = _g("SCOPES") or "openid email profile"
        scopes = [s for s in scopes_raw.split() if s]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        slug = name.lower()
        found[slug] = OIDCProviderConfig(
            slug=slug,
            display_name=display_name,
            issuer=issuer.rstrip("/"),
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
    return found


# ---------- Discovery / metadata ----------

def discover(provider: OIDCProviderConfig) -> dict[str, Any]:
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

def begin_login(provider: OIDCProviderConfig, redirect_uri: str) -> str:
    """Build the IdP authorization URL and stash pending state. Returns
    the URL the user agent should be redirected to."""
    meta = discover(provider)
    auth_url = meta["authorization_endpoint"]

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
        "provider": provider.slug,
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


def complete_login(db: DBSession, state: str, code: str) -> dict[str, Any]:
    """Validate state, exchange the code for tokens, validate the ID token,
    and return the parsed claims. Raises ValueError on any failure.
    """
    pending = _pending_states.pop(state, None)
    if pending is None:
        raise ValueError("unknown or expired state")
    if time.time() - pending["created_at"] > _STATE_TTL_SECONDS:
        raise ValueError("state expired")

    provider = get_provider(db, pending["provider"])
    if provider is None:
        raise ValueError(f"provider {pending['provider']} no longer configured")

    meta = discover(provider)
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
