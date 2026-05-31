"""Admin CRUD for OIDC providers.

Mounted at /providers/oidc/* by main.py with require_admin on the
include-router level. The client secret is write-only — never returned
by GET responses — and only updated when explicitly passed on PATCH.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.core.auth.deps import current_admin
from app.core.auth.models.oidc_provider import OIDCProvider
from app.core.auth.models.user import User
from app.core.auth.schemas import OIDCProviderCreate, OIDCProviderRead, OIDCProviderUpdate
from app.core.auth.services.oidc import encrypt_client_secret, invalidate_discovery_cache
from app.db import get_db

router = APIRouter(prefix="/providers/oidc", tags=["providers"])


def _to_read(p: OIDCProvider) -> OIDCProviderRead:
    return OIDCProviderRead.model_validate(
        {
            "id": p.id,
            "slug": p.slug,
            "display_name": p.display_name,
            "issuer": p.issuer,
            "client_id": p.client_id,
            "scopes": p.scopes,
            "enabled": p.enabled,
            "trusted_identity": p.trusted_identity,
            "has_client_secret": p.encrypted_client_secret is not None,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
    )


@router.get("", response_model=list[OIDCProviderRead])
def list_providers(db: DBSession = Depends(get_db), _: User = Depends(current_admin)):
    return [_to_read(p) for p in db.query(OIDCProvider).order_by(OIDCProvider.slug).all()]


@router.post("", response_model=OIDCProviderRead, status_code=201)
def create_provider(
    payload: OIDCProviderCreate,
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    p = OIDCProvider(
        slug=payload.slug.lower(),
        display_name=payload.display_name,
        issuer=payload.issuer.rstrip("/"),
        client_id=payload.client_id,
        encrypted_client_secret=encrypt_client_secret(payload.client_secret),
        scopes=payload.scopes,
        enabled=payload.enabled,
        trusted_identity=payload.trusted_identity,
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="slug already in use") from exc
    db.refresh(p)
    invalidate_discovery_cache(p.issuer)
    return _to_read(p)


@router.patch("/{provider_id}", response_model=OIDCProviderRead)
def update_provider(
    provider_id: int,
    payload: OIDCProviderUpdate,
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    p = db.get(OIDCProvider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="not found")

    old_issuer = p.issuer
    if payload.display_name is not None:
        p.display_name = payload.display_name
    if payload.issuer is not None:
        p.issuer = payload.issuer.rstrip("/")
    if payload.client_id is not None:
        p.client_id = payload.client_id
    if payload.client_secret is not None:
        # Only re-encrypt when an actual value comes through — empty/None
        # is "keep the existing one." The UI sends None for unchanged.
        p.encrypted_client_secret = encrypt_client_secret(payload.client_secret)
    if payload.scopes is not None:
        p.scopes = payload.scopes
    if payload.enabled is not None:
        p.enabled = payload.enabled
    if payload.trusted_identity is not None:
        p.trusted_identity = payload.trusted_identity

    db.commit()
    db.refresh(p)
    invalidate_discovery_cache(old_issuer)
    invalidate_discovery_cache(p.issuer)
    return _to_read(p)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    p = db.get(OIDCProvider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="not found")
    issuer = p.issuer
    db.delete(p)
    db.commit()
    invalidate_discovery_cache(issuer)
    from fastapi.responses import Response as RawResponse
    return RawResponse(status_code=204)
