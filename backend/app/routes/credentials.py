"""Credential CRUD."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.crypto import encrypt
from app.db import get_db
from app.models.credential import Credential
from app.models.enums import AuthType
from app.schemas import CredentialCreate, CredentialFromUserpass, CredentialRead
from app.services.credentials import encode_secret
from app.services.pan_client import keygen

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialRead])
def list_credentials(db: Session = Depends(get_db)):
    return db.query(Credential).order_by(Credential.name).all()


@router.post("", response_model=CredentialRead, status_code=201)
def create_credential(payload: CredentialCreate, db: Session = Depends(get_db)):
    plaintext = encode_secret(
        payload.auth_type,
        api_key=payload.api_key,
        username=payload.username,
        password=payload.password,
    )
    cred = Credential(
        name=payload.name,
        description=payload.description,
        auth_type=payload.auth_type,
        scope=payload.scope,
        encrypted_secret=encrypt(plaintext),
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


@router.post("/from-userpass", response_model=CredentialRead, status_code=201)
def create_credential_from_userpass(
    payload: CredentialFromUserpass, db: Session = Depends(get_db)
):
    """Use username+password once to mint an API key, then store ONLY the key.

    Equivalent to running `/api/?type=keygen` against the target. Works the
    same way whether the target is a firewall or a Panorama.
    """
    try:
        api_key = keygen(
            payload.target_hostname,
            payload.username,
            payload.password,
            verify_tls=payload.verify_tls,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"keygen failed: {exc}") from exc

    cred = Credential(
        name=payload.name,
        description=payload.description or f"API key minted from {payload.username}@{payload.target_hostname}",
        auth_type=AuthType.API_KEY,
        scope=payload.scope,
        encrypted_secret=encrypt(api_key),
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


@router.delete("/{cred_id}", status_code=204)
def delete_credential(cred_id: int, db: Session = Depends(get_db)):
    cred = db.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(cred)
    db.commit()
