"""Panorama CRUD + on-demand sync."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.panorama import Panorama
from app.schemas import PanoramaCreate, PanoramaRead
from app.services.credentials import resolve as resolve_credential
from app.services.panorama_client import PanoramaClient
from app.services.panorama_sync import sync_panorama

router = APIRouter(prefix="/panoramas", tags=["panoramas"])


@router.get("", response_model=list[PanoramaRead])
def list_panoramas(db: Session = Depends(get_db)):
    return db.query(Panorama).order_by(Panorama.name).all()


@router.post("", response_model=PanoramaRead, status_code=201)
def create_panorama(payload: PanoramaCreate, db: Session = Depends(get_db)):
    pano = Panorama(
        name=payload.name,
        hostname=payload.hostname,
        credential_id=payload.credential_id,
        verify_tls=payload.verify_tls,
    )
    db.add(pano)
    db.commit()
    db.refresh(pano)
    return pano


@router.patch("/{pano_id}", response_model=PanoramaRead)
def update_panorama(pano_id: int, payload: PanoramaCreate, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(pano, k, v)
    db.commit()
    db.refresh(pano)
    return pano


@router.post("/{pano_id}/test-connection")
def test_panorama(pano_id: int, db: Session = Depends(get_db)):
    """Hit Panorama with `show system info` and return what it told us."""
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    cred = resolve_credential(pano.credential)
    client = PanoramaClient(pano.hostname, cred, verify_tls=pano.verify_tls)
    try:
        info = client.test_connection()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "info": info}


@router.post("/{pano_id}/sync", response_model=PanoramaRead)
def sync(pano_id: int, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        sync_panorama(db, pano)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.refresh(pano)
    return pano


@router.delete("/{pano_id}", status_code=204)
def delete_panorama(pano_id: int, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(pano)
    db.commit()
