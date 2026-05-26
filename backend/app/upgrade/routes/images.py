"""HTTP surface for PAN-OS image registry.

Scope of this PR:
- List registered images.
- Register an image *by version only* (devices pull from
  updates.paloaltonetworks.com at orchestration time).
- Delete an image registration.

Out of scope (follow-up): multipart upload of an actual .img file with
on-disk storage. The model already supports it (filename + sha256 +
size_bytes), but the upload UX needs streaming + a writable storage
path on the read-only-rootfs backend pod, which is its own design
question. Until then, all registered images are "version-only" and
`uploaded` reads as False in the API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.upgrade.models.image import PanosImage
from app.upgrade.schemas import ImageCreate, ImageRead

router = APIRouter(tags=["upgrade"])


def _to_read(img: PanosImage) -> ImageRead:
    return ImageRead.model_validate(
        {
            "id": img.id,
            "version": img.version,
            "filename": img.filename,
            "sha256": img.sha256,
            "size_bytes": img.size_bytes,
            "notes": img.notes,
            "created_at": img.created_at,
            "uploaded": img.filename is not None,
        }
    )


@router.get("/upgrade/images", response_model=list[ImageRead])
def list_images(db: Session = Depends(get_db)):
    rows = (
        db.execute(select(PanosImage).order_by(PanosImage.version.desc()))
        .scalars()
        .all()
    )
    return [_to_read(r) for r in rows]


@router.post(
    "/upgrade/images",
    response_model=ImageRead,
    status_code=status.HTTP_201_CREATED,
)
def register_image(payload: ImageCreate, db: Session = Depends(get_db)):
    """Register a PAN-OS version that devices will pull themselves.

    No `filename` is set on the row — the orchestrator's
    "device_pull_image=True" path on the job uses this to mean
    "tell the device to download X.Y.Z from
    updates.paloaltonetworks.com." When upload lands, the same model
    will gain non-null filename/sha256/size_bytes, and `uploaded`
    flips to True on read.

    Duplicate-version registration is allowed (the version string is
    indexed but not unique on the model) — operators sometimes
    register the same version twice with different notes (e.g. "for
    the Q1 fleet refresh" vs "for the Azure HoneyPot only"). The job
    creation step picks by id, so duplicates don't cause routing
    ambiguity.
    """
    img = PanosImage(
        version=payload.version,
        notes=payload.notes,
        filename=None,
        sha256=None,
        size_bytes=None,
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return _to_read(img)


@router.delete(
    "/upgrade/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_image(image_id: int, db: Session = Depends(get_db)):
    """Remove an image registration.

    Does NOT cascade to jobs that reference this image — those jobs
    keep their `image_id` FK pointing at a now-dangling row. That's
    intentional: deleting a job's image mid-run shouldn't quietly
    invalidate the job. The orchestrator handles the missing-image
    case gracefully (it'll fail the per-device task with a clear
    error rather than crashing the worker).

    Future enhancement: refuse delete if any RUNNING jobs reference
    the image. Out of scope for this PR.
    """
    img = db.get(PanosImage, image_id)
    if img is None:
        raise HTTPException(status_code=404, detail="image not found")
    db.delete(img)
    db.commit()
