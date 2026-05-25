"""Rotate FERNET_KEY across every encrypted blob in the DB.

Per the merge plan + MIGRATION_NOTES §7.1. The merged app generates a
fresh FERNET key at phase-2e cutover and uses this script to re-encrypt
every existing encrypted column from the old key to the new one.

Encrypted columns covered:
- `devices.encrypted_api_key`            (Fernet device API keys, nullable)
- `panoramas.encrypted_api_key`          (Fernet Panorama API keys, nullable)
- `oidc_providers.encrypted_client_secret` (Fernet OIDC client secrets, NOT NULL)
- `users.encrypted_totp_secret`          (Fernet TOTP base32 secrets, nullable)

## Usage

Dry-run (default — no writes):

    OLD_FERNET_KEY=... NEW_FERNET_KEY=... DATABASE_URL=... \\
        python -m scripts.rotate_fernet

Apply (writes; single transaction per table):

    OLD_FERNET_KEY=... NEW_FERNET_KEY=... DATABASE_URL=... \\
        python -m scripts.rotate_fernet --apply

## Safety properties

- **Read-decrypt-encrypt-verify before write.** Every row is verified to
  round-trip through the NEW key before any UPDATE is queued.
- **One transaction per table.** A mid-table failure rolls back that
  table's updates; other tables that already committed stay rotated.
  (Cross-table atomicity isn't needed — each blob is independently
  encrypted; partial rotation leaves some rows on OLD, some on NEW,
  both decryptable as long as both keys are still available in env.)
- **Idempotent.** If a blob already decrypts with the NEW key (e.g. a
  prior run was killed mid-table), the row is skipped, not re-encrypted.
  Safe to rerun on a partial state.
- **No app.config import.** Reads keys + URL directly from env to avoid
  the "app.config loaded FERNET_KEY at import time and that locks
  app.crypto to one key" trap.

## Exit codes

- 0  success (dry-run or apply)
- 1  apply mode — at least one row failed to verify-round-trip; nothing
     was written for the table(s) that hit failures (rolled back)
- 2  pre-flight failure (DB unreachable, keys malformed, etc.)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EncryptedColumn:
    """One column to walk during rotation."""

    table: str
    pk: str
    column: str

    def select_sql(self) -> str:
        return (
            f"SELECT {self.pk}, {self.column} FROM {self.table} "
            f"WHERE {self.column} IS NOT NULL"
        )

    def update_sql(self) -> str:
        return f"UPDATE {self.table} SET {self.column} = :blob WHERE {self.pk} = :pk"


# Order doesn't matter — each blob is independently encrypted. Listed in
# rough "operational importance" order so a partial run prioritizes the
# most-used credential types.
COLUMNS: tuple[EncryptedColumn, ...] = (
    EncryptedColumn("devices", "id", "encrypted_api_key"),
    EncryptedColumn("panoramas", "id", "encrypted_api_key"),
    EncryptedColumn("oidc_providers", "id", "encrypted_client_secret"),
    EncryptedColumn("users", "id", "encrypted_totp_secret"),
)


@dataclass
class TableResult:
    rotated: int = 0
    already_new: int = 0
    failed: int = 0


def _decrypt_with(fernet: Fernet, blob: bytes) -> bytes | None:
    """Return the plaintext, or None if blob doesn't decrypt with this key."""
    try:
        return fernet.decrypt(blob)
    except InvalidToken:
        return None


def _rotate_one_table(
    conn: Connection,
    col: EncryptedColumn,
    old: Fernet,
    new: Fernet,
    apply: bool,
) -> TableResult:
    res = TableResult()
    rows = conn.execute(text(col.select_sql())).all()
    for pk, raw in rows:
        # SQLAlchemy returns LargeBinary as bytes; defensive coerce.
        blob = bytes(raw)

        plain = _decrypt_with(old, blob)
        if plain is None:
            # Maybe already rotated?
            if _decrypt_with(new, blob) is not None:
                res.already_new += 1
                log.info("%s.%s=%s: already on new key, skipping",
                         col.table, col.pk, pk)
                continue
            res.failed += 1
            log.error(
                "%s.%s=%s: blob decrypts with neither OLD nor NEW key. "
                "Cannot rotate this row.",
                col.table, col.pk, pk,
            )
            continue

        new_blob = new.encrypt(plain)

        # Round-trip verify before considering this row good.
        if _decrypt_with(new, new_blob) != plain:
            res.failed += 1
            log.error("%s.%s=%s: NEW-key round-trip verify FAILED, "
                      "refusing to write", col.table, col.pk, pk)
            continue

        if apply:
            conn.execute(
                text(col.update_sql()),
                {"blob": new_blob, "pk": pk},
            )
        res.rotated += 1
    return res


def _load_keys() -> tuple[Fernet, Fernet]:
    try:
        old = Fernet(os.environ["OLD_FERNET_KEY"].encode("ascii"))
        new = Fernet(os.environ["NEW_FERNET_KEY"].encode("ascii"))
    except KeyError as e:
        log.error("Missing required env var: %s", e)
        raise SystemExit(2)
    except Exception as e:
        log.error("Failed to load Fernet keys: %s", e)
        raise SystemExit(2)
    if os.environ["OLD_FERNET_KEY"] == os.environ["NEW_FERNET_KEY"]:
        log.error("OLD_FERNET_KEY and NEW_FERNET_KEY are identical; refusing "
                  "to rotate (would be a no-op that masks misconfiguration).")
        raise SystemExit(2)
    return old, new


def rotate(database_url: str, old: Fernet, new: Fernet, apply: bool) -> int:
    """Walk every encrypted column and rotate. Returns 0 / 1 exit code."""
    engine = create_engine(database_url, future=True)

    total_rotated = 0
    total_already_new = 0
    total_failed = 0

    # One transaction per table — failure in one doesn't roll back others.
    for col in COLUMNS:
        with engine.begin() as conn:
            res = _rotate_one_table(conn, col, old, new, apply)
            total_rotated += res.rotated
            total_already_new += res.already_new
            total_failed += res.failed
            mode = "applied" if apply else "dry-run"
            log.info(
                "%s.%s: %d rotated (%s), %d already-on-new, %d failed",
                col.table, col.column, res.rotated, mode,
                res.already_new, res.failed,
            )
            if res.failed > 0 and apply:
                # Roll back this table's UPDATEs. Other tables that
                # already committed stay rotated.
                log.error("%s: rolling back transaction due to %d failure(s)",
                          col.table, res.failed)
                conn.rollback()
                return 1

    log.info(
        "TOTAL: %d rotated (%s), %d already-on-new, %d failed",
        total_rotated, "applied" if apply else "dry-run",
        total_already_new, total_failed,
    )
    return 0 if total_failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write rotated blobs. Default is dry-run.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable per-row INFO logging (default is summary only).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    old, new = _load_keys()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL is not set")
        return 2

    if not args.apply:
        log.info(
            "DRY-RUN mode — no writes. Pass --apply to commit rotation."
        )

    return rotate(database_url, old, new, args.apply)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
