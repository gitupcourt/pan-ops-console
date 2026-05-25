"""PAN-OS image upload and listing (stubbed)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.db import get_db
from app.models.image import PanosImage
from app.models.user import User

router = APIRouter(prefix="/api/images", tags=["images"])


class ImageOut(BaseModel):
    id: int
    version: str
    filename: str | None
    sha256: str | None
    size_bytes: int | None
    notes: str | None

    class Config:
        from_attributes = True


@router.get("", response_model=list[ImageOut])
def list_images(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[PanosImage]:
    return db.query(PanosImage).order_by(PanosImage.version).all()


@router.post("/upload", response_model=ImageOut)
def upload_image(
    _db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """TODO: accept multipart upload, stream to IMAGE_STORAGE_PATH, hash, persist row."""
    raise HTTPException(501, "Image upload not yet implemented")


class ImageReference(BaseModel):
    """Register a version that devices will pull themselves (no upload)."""
    version: str
    notes: str | None = None


@router.post("/reference", response_model=ImageOut, status_code=201)
def register_reference(
    payload: ImageReference,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> PanosImage:
    img = PanosImage(version=payload.version, notes=payload.notes)
    db.add(img)
    db.commit()
    db.refresh(img)
    return img
