"""Credential management. Secrets are encrypted at rest via Fernet and never returned."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.crypto import encrypt
from app.db import get_db
from app.models.credential import Credential
from app.models.enums import AuthType, CredentialScope
from app.models.user import User

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CredentialIn(BaseModel):
    name: str
    description: str | None = None
    scope: CredentialScope
    auth_type: AuthType
    api_key: str | None = Field(default=None, description="Required when auth_type=api_key")
    username: str | None = None
    password: str | None = None


class CredentialOut(BaseModel):
    id: int
    name: str
    description: str | None
    scope: CredentialScope
    auth_type: AuthType

    class Config:
        from_attributes = True


@router.get("", response_model=list[CredentialOut])
def list_credentials(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[Credential]:
    return db.query(Credential).order_by(Credential.name).all()


@router.post("", response_model=CredentialOut, status_code=201)
def create_credential(
    payload: CredentialIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> Credential:
    if payload.auth_type == AuthType.API_KEY:
        if not payload.api_key:
            raise HTTPException(400, "api_key is required when auth_type=api_key")
        secret = payload.api_key
    else:
        if not (payload.username and payload.password):
            raise HTTPException(400, "username and password required when auth_type=userpass")
        secret = json.dumps({"username": payload.username, "password": payload.password})

    cred = Credential(
        name=payload.name,
        description=payload.description,
        scope=payload.scope,
        auth_type=payload.auth_type,
        encrypted_secret=encrypt(secret),
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


@router.delete("/{cred_id}", status_code=204)
def delete_credential(
    cred_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    cred = db.get(Credential, cred_id)
    if not cred:
        raise HTTPException(404, "Credential not found")
    db.delete(cred)
    db.commit()
