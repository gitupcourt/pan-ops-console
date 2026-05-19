"""Credential CRUD."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.crypto import encrypt
from app.db import get_db
from app.models.credential import Credential
from app.schemas import CredentialCreate, CredentialRead
from app.services.credentials import encode_secret

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


@router.delete("/{cred_id}", status_code=204)
def delete_credential(cred_id: int, db: Session = Depends(get_db)):
    cred = db.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(cred)
    db.commit()
