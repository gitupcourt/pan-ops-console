"""Re-encrypt the credentials table with a new FERNET_KEY.

Run this AFTER updating the cluster's pan-fw-upgrader-secrets to a new
key but BEFORE bouncing the panfw-api / panfw-worker / panfw-beat
Deployments. The flow is:

  1. Update SealedSecret in homelab, apply.
  2. The unsealed Secret now has the NEW key but the running pods still
     have the OLD key in their env (env is injected at pod start).
  3. From your laptop:
       kubectl exec -i deploy/panfw-api -- env OLD_FERNET_KEY='<old>' \\
                                              NEW_FERNET_KEY='<new>' \\
                                              python /app/scripts/rotate_fernet.py
     This pod is running with the old key but we pass both via env. The
     script ignores settings.FERNET_KEY entirely and uses these two
     directly. Credentials get decrypted with OLD, re-encrypted with NEW,
     written back. All inside a single transaction.
  4. Bounce the Deployments. They restart with the new key in their env
     and can now decrypt the freshly-re-encrypted credentials.

Why this order: if you bounce the pods BEFORE re-encrypting, the new
pods come up with NEW_FERNET_KEY but the DB still has rows encrypted
with OLD_FERNET_KEY. Every device probe / pre-check that needs to
decrypt a credential would fail until you complete the rotation.
Re-encrypting first means the cluster is broken for a few seconds at
most (just the rollout itself), not for the duration of the manual
rotation.

Why not import app.crypto: that module reads settings.FERNET_KEY at
import time and binds Fernet to it. We need TWO Fernet instances — one
for decrypt-old and one for encrypt-new — so we bypass app.crypto and
use the cryptography library directly.

Idempotent in the failure case: if the script crashes mid-loop, the
transaction rolls back and no rows are touched. Safe to re-run.

This script doesn't touch any other table. The only column with
Fernet-encrypted data in this schema is `credentials.encrypted_secret`.
If that ever changes (e.g. we add encrypted columns elsewhere) update
this script alongside.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `from app...` work regardless of where this script is invoked
# from. The container's app code lives at /app; locally the same path
# is the backend's working dir under the running container.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from cryptography.fernet import Fernet, InvalidToken

# Use the app's DB session machinery so we get the same connection
# string the live containers do. This is the one app-module import
# we do — it doesn't touch the crypto layer.
from app.db import SessionLocal
from app.models.credential import Credential


def main() -> int:
    old_key = os.environ.get("OLD_FERNET_KEY")
    new_key = os.environ.get("NEW_FERNET_KEY")
    if not old_key or not new_key:
        print(
            "ERROR: OLD_FERNET_KEY and NEW_FERNET_KEY env vars are required.",
            file=sys.stderr,
        )
        return 2
    if old_key == new_key:
        print(
            "ERROR: OLD_FERNET_KEY == NEW_FERNET_KEY — nothing to rotate.",
            file=sys.stderr,
        )
        return 2

    try:
        old_fernet = Fernet(old_key.encode())
        new_fernet = Fernet(new_key.encode())
    except ValueError as exc:
        # Wrong length / bad encoding — Fernet keys are 32 url-safe base64
        # bytes plus the trailing `=`. Bad input here is unrecoverable; let
        # the operator regenerate.
        print(f"ERROR: invalid Fernet key: {exc}", file=sys.stderr)
        return 2

    db = SessionLocal()
    rows_touched = 0
    try:
        creds = db.query(Credential).all()
        if not creds:
            print("No credentials to rotate. Done.")
            return 0
        print(f"Found {len(creds)} credential rows to re-encrypt.")
        for cred in creds:
            try:
                plaintext = old_fernet.decrypt(cred.encrypted_secret)
            except InvalidToken:
                # If decrypt fails, this row was either (a) never encrypted
                # with OLD (so OLD_FERNET_KEY doesn't match what we think),
                # or (b) already rotated to NEW. Try NEW; if that works the
                # row is already rotated and we skip it.
                try:
                    new_fernet.decrypt(cred.encrypted_secret)
                    print(
                        f"  - cred id={cred.id} ({cred.name}) already on NEW key; skipping"
                    )
                    continue
                except InvalidToken:
                    print(
                        f"ERROR: credential id={cred.id} ({cred.name}) cannot be "
                        f"decrypted with EITHER key. Aborting before any writes.",
                        file=sys.stderr,
                    )
                    db.rollback()
                    return 1
            # Decrypted with OLD; re-encrypt with NEW.
            cred.encrypted_secret = new_fernet.encrypt(plaintext)
            rows_touched += 1
        db.commit()
        print(f"OK. Re-encrypted {rows_touched} credential row(s) with the new key.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
