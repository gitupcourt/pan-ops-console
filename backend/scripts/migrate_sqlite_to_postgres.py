"""One-shot data migration: SQLite → Postgres + FERNET key rotation.

Phase 2e of the merge plan. Reads every row from the existing prod
SQLite DB (capacity polling's working state), decrypts encrypted
columns with OLD_FERNET_KEY, re-encrypts with NEW_FERNET_KEY, writes
to a fresh Postgres DB. Single one-shot operation; idempotent re-runs
require `--truncate-dest`.

## Encrypted columns covered

Same set as scripts/rotate_fernet.py — kept in sync. Modify both if a
new encrypted column appears.

| Table | Column | Nullable |
|---|---|---|
| `devices` | `encrypted_api_key` | yes |
| `panoramas` | `encrypted_api_key` | yes |
| `oidc_providers` | `encrypted_client_secret` | NOT NULL |
| `users` | `encrypted_totp_secret` | yes |

Non-encrypted columns (sessions, backup_codes, samples) are copied
verbatim.

## Usage

Dry-run (default — no writes; verifies decrypt-encrypt-roundtrip works
for every blob):

    SQLITE_PATH=/path/to/capacity.db \\
    DATABASE_URL_DEST=postgresql+psycopg://panops:pw@postgres:5432/panops \\
    OLD_FERNET_KEY=... NEW_FERNET_KEY=... \\
      python -m scripts.migrate_sqlite_to_postgres

Apply (writes):

    SQLITE_PATH=... DATABASE_URL_DEST=... OLD_FERNET_KEY=... NEW_FERNET_KEY=... \\
      python -m scripts.migrate_sqlite_to_postgres --apply

Truncate dest tables first (DANGEROUS — wipes any data in Postgres):

    ... python -m scripts.migrate_sqlite_to_postgres --apply --truncate-dest

## Safety properties

- **Dry-run by default.** No INSERTs without --apply.
- **One transaction per table.** Mid-table failure rolls back that
  table's INSERTs; tables migrated before stay migrated. Cross-table
  atomicity isn't needed because the FK-dependency order means a
  partial migration is recoverable (resume the failing table after fix).
- **ID preservation.** Source row IDs are preserved in dest so FKs
  resolve correctly. After bulk INSERT, the sequence is advanced to
  MAX(id)+1 so subsequent app-driven inserts don't collide.
- **Decrypt-encrypt-verify-write per encrypted blob.** Every blob
  round-trips through the NEW key before INSERT; refuses to write if
  it doesn't.
- **No app.config import** — same trap as rotate_fernet.py. Reads
  config directly from env so the script's process doesn't lock
  app.crypto to one of the two keys.
- **Order respects FKs.**
  users → oidc_providers → panoramas → devices → sessions →
  backup_codes → samples.

## What it does NOT do

- Doesn't touch the upgrade-module tables (upgrade_jobs etc.) — those
  were added in phase 4c-models and are empty in the SQLite source.
- Doesn't validate that the source's schema is exactly 0001-era; if
  someone manually edited the schema, results are undefined. (Sane
  for a one-shot migration; the SQLite source is a known-good prod DB.)
- Doesn't run alembic. Run `alembic upgrade head` against the dest
  BEFORE this script — the dest tables must exist for the INSERTs to
  succeed.

## Exit codes

- 0  success
- 1  --apply was passed but at least one table failed
- 2  pre-flight failure (DB unreachable, keys malformed, etc.)
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Table specifications
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class TableSpec:
    """How to migrate one table."""

    name: str
    columns: tuple[str, ...]
    encrypted_columns: tuple[str, ...] = ()
    # Postgres sequence name for the PK after INSERT. Derived from
    # table_<id_col>_seq by convention; explicit here to handle the
    # `sessions` case where the PK is `token_hash` (no sequence).
    pk_sequence: str | None = None

    def select_sql(self) -> str:
        return f"SELECT {', '.join(self.columns)} FROM {self.name}"

    def insert_sql(self) -> str:
        placeholders = ", ".join(f":{c}" for c in self.columns)
        return (
            f"INSERT INTO {self.name} ({', '.join(self.columns)}) "
            f"VALUES ({placeholders})"
        )


# FK-dependency order: users → oidc_providers → panoramas → devices →
# sessions → backup_codes → samples.
TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="users",
        columns=(
            "id", "username", "email", "password_hash",
            "is_admin", "is_active",
            "encrypted_totp_secret", "totp_enabled",
            "created_at", "updated_at", "last_login_at",
        ),
        encrypted_columns=("encrypted_totp_secret",),
        pk_sequence="users_id_seq",
    ),
    TableSpec(
        name="oidc_providers",
        columns=(
            "id", "slug", "display_name", "issuer",
            "client_id", "encrypted_client_secret",
            "scopes", "enabled",
            "created_at", "updated_at",
        ),
        encrypted_columns=("encrypted_client_secret",),
        pk_sequence="oidc_providers_id_seq",
    ),
    TableSpec(
        name="panoramas",
        columns=(
            "id", "name", "hostname",
            "encrypted_api_key", "verify_tls",
            "created_at", "last_sync_at",
            "reachable", "last_reachability_at", "last_reachability_error",
        ),
        encrypted_columns=("encrypted_api_key",),
        pk_sequence="panoramas_id_seq",
    ),
    TableSpec(
        name="devices",
        # 0001-era columns only — the new phase-4a columns are not in
        # the SQLite source. Dest Postgres has them; they fill in with
        # NULL / server_defaults for migrated rows.
        columns=(
            "id", "name", "hostname", "ip_address",
            "serial", "model", "sw_version",
            "source", "panorama_id",
            "encrypted_api_key", "verify_tls",
            "proxy_via_panorama", "polling_enabled",
            "created_at", "last_poll_at", "last_poll_error",
        ),
        encrypted_columns=("encrypted_api_key",),
        pk_sequence="devices_id_seq",
    ),
    TableSpec(
        name="sessions",
        columns=(
            "token_hash", "user_id",
            "created_at", "expires_at", "last_seen_at", "user_agent",
        ),
        # PK is token_hash (string), not an integer with a sequence.
        pk_sequence=None,
    ),
    TableSpec(
        name="backup_codes",
        columns=("id", "user_id", "code_hash", "created_at"),
        pk_sequence="backup_codes_id_seq",
    ),
    TableSpec(
        name="samples",
        columns=(
            "id", "device_id", "metric", "ts",
            "current_value", "max_value", "pct",
        ),
        pk_sequence="samples_id_seq",
    ),
)


@dataclass
class TableResult:
    inserted: int = 0
    verified_blobs: int = 0
    failed_blobs: int = 0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _decrypt_with(fernet: Fernet, blob: bytes) -> bytes | None:
    try:
        return fernet.decrypt(blob)
    except InvalidToken:
        return None


def _reencrypt(
    blob: bytes | None, old: Fernet, new: Fernet, *, where: str
) -> bytes | None:
    """Decrypt with OLD, re-encrypt with NEW, verify round-trip.

    Returns the new ciphertext, or raises ValueError if the blob can't
    round-trip. None blobs pass through unchanged.
    """
    if blob is None:
        return None
    blob = bytes(blob)
    plain = _decrypt_with(old, blob)
    if plain is None:
        # Already on NEW? Idempotent re-run case.
        if _decrypt_with(new, blob) is not None:
            return blob
        raise ValueError(
            f"{where}: blob decrypts with neither OLD nor NEW key"
        )
    new_blob = new.encrypt(plain)
    if _decrypt_with(new, new_blob) != plain:
        raise ValueError(f"{where}: NEW-key round-trip verify FAILED")
    return new_blob


def _row_to_dict(spec: TableSpec, row: sqlite3.Row) -> dict[str, Any]:
    """SQLite Row → dict keyed by column name."""
    return {col: row[col] for col in spec.columns}


def _migrate_table(
    *,
    source: sqlite3.Connection,
    dest_conn: Connection,
    spec: TableSpec,
    old: Fernet,
    new: Fernet,
    apply: bool,
    truncate_dest: bool,
) -> TableResult:
    res = TableResult()

    if truncate_dest and apply:
        if dest_conn.dialect.name == "postgresql":
            # Restart identity so the post-INSERT sequence advance
            # lands cleanly. CASCADE handles dependent rows for tables
            # we'd be re-populating in the same run.
            dest_conn.execute(
                text(f"TRUNCATE TABLE {spec.name} RESTART IDENTITY CASCADE")
            )
        else:
            # SQLite path (pytest-only): plain DELETE; SQLite resets
            # AUTOINCREMENT counters via sqlite_sequence which we don't
            # need for tests.
            dest_conn.execute(text(f"DELETE FROM {spec.name}"))

    cursor = source.execute(spec.select_sql())
    rows = cursor.fetchall()
    log.info("%s: %d rows in source", spec.name, len(rows))

    max_id: int | None = None
    pk_col = "id" if "id" in spec.columns else None

    for row in rows:
        data = _row_to_dict(spec, row)

        for enc_col in spec.encrypted_columns:
            try:
                data[enc_col] = _reencrypt(
                    data[enc_col],
                    old,
                    new,
                    where=f"{spec.name}.{enc_col} (row {data.get('id', '?')})",
                )
                if data[enc_col] is not None:
                    res.verified_blobs += 1
            except ValueError as exc:
                res.failed_blobs += 1
                log.error("%s", exc)
                # Skip this row — don't write a row with un-rotated key.
                continue

        if apply:
            dest_conn.execute(text(spec.insert_sql()), data)
        res.inserted += 1

        if pk_col and isinstance(data.get(pk_col), int):
            if max_id is None or data[pk_col] > max_id:
                max_id = data[pk_col]

    # Advance the Postgres sequence past the highest ID we inserted,
    # so subsequent app-driven inserts don't collide. Postgres-only —
    # SQLite uses INTEGER PRIMARY KEY autoincrement and doesn't need
    # an explicit sequence bump; pytest-using-SQLite-for-dest tests
    # exercise that path implicitly.
    if (
        apply
        and spec.pk_sequence
        and max_id is not None
        and dest_conn.dialect.name == "postgresql"
    ):
        dest_conn.execute(
            text("SELECT setval(:seq, :val, true)"),
            {"seq": spec.pk_sequence, "val": max_id},
        )

    log.info(
        "%s: %s — %d %s, %d verified blobs, %d failed blobs",
        spec.name,
        "applied" if apply else "dry-run",
        res.inserted,
        "inserted" if apply else "would-insert",
        res.verified_blobs,
        res.failed_blobs,
    )
    return res


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

def _load_keys() -> tuple[Fernet, Fernet]:
    try:
        old_key = os.environ["OLD_FERNET_KEY"]
        new_key = os.environ["NEW_FERNET_KEY"]
    except KeyError as e:
        log.error("Missing required env var: %s", e)
        raise SystemExit(2)
    try:
        old = Fernet(old_key.encode("ascii"))
        new = Fernet(new_key.encode("ascii"))
    except Exception as e:
        log.error("Failed to construct Fernet from keys: %s", e)
        raise SystemExit(2)
    return old, new


def _connect_source() -> sqlite3.Connection:
    path = os.environ.get("SQLITE_PATH")
    if not path:
        log.error("SQLITE_PATH env var is required")
        raise SystemExit(2)
    if not os.path.exists(path):
        log.error("SQLITE_PATH does not exist: %s", path)
        raise SystemExit(2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _dest_engine() -> Engine:
    url = os.environ.get("DATABASE_URL_DEST")
    if not url:
        log.error("DATABASE_URL_DEST env var is required")
        raise SystemExit(2)
    return create_engine(url, future=True)


def migrate(
    *,
    source: sqlite3.Connection,
    dest_engine: Engine,
    old: Fernet,
    new: Fernet,
    apply: bool,
    truncate_dest: bool,
) -> int:
    """Walk every table and migrate. Returns 0 / 1 exit code."""
    total_inserted = 0
    total_verified = 0
    total_failed = 0

    for spec in TABLES:
        with dest_engine.begin() as conn:
            res = _migrate_table(
                source=source,
                dest_conn=conn,
                spec=spec,
                old=old,
                new=new,
                apply=apply,
                truncate_dest=truncate_dest,
            )
            total_inserted += res.inserted
            total_verified += res.verified_blobs
            total_failed += res.failed_blobs
            if res.failed_blobs > 0 and apply:
                log.error(
                    "%s: rolling back transaction due to %d failed blobs",
                    spec.name, res.failed_blobs,
                )
                conn.rollback()
                return 1

    log.info(
        "TOTAL: %s — %d %s rows, %d verified blobs, %d failed blobs",
        "applied" if apply else "dry-run",
        total_inserted,
        "inserted" if apply else "would-insert",
        total_verified,
        total_failed,
    )
    return 0 if total_failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write to Postgres. Default is dry-run.",
    )
    parser.add_argument(
        "--truncate-dest", action="store_true",
        help=(
            "DANGEROUS: TRUNCATE every dest table before INSERTing. "
            "Wipes any pre-existing rows in the Postgres dest. Use only "
            "when re-running after a partial failed migration."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Per-row INFO logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.apply:
        log.info("DRY-RUN — no writes. Pass --apply to commit migration.")
    if args.truncate_dest and not args.apply:
        log.info("--truncate-dest specified but --apply is not; ignoring truncate.")

    old, new = _load_keys()
    source = _connect_source()
    engine = _dest_engine()

    try:
        return migrate(
            source=source,
            dest_engine=engine,
            old=old,
            new=new,
            apply=args.apply,
            truncate_dest=args.truncate_dest,
        )
    finally:
        source.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
