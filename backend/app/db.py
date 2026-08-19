"""SQLAlchemy engine and session management.

Engine config is dialect-aware:

- **PostgreSQL** — every deployment. Explicit pool size + recycle so the
  Celery workers + API don't accumulate stale connections through Postgres's
  idle-disconnect window; `pool_pre_ping` catches a half-dropped connection
  before a query fails. Set
  `DATABASE_URL=postgresql+psycopg://user:pass@host:port/db` (the `+psycopg`
  qualifier selects psycopg3, which is what's installed).
- **SQLite** — the **test suite only**. `check_same_thread=False`; one
  connection at a time. This is how CI runs the tests without a database
  service. It is NOT a deployment option: polling, alerting, and upgrade
  orchestration all run on Celery + Redis (multiple processes), and several
  processes against one SQLite file serialize and throw "database is locked".
  See docs/DEPLOYMENT.md.

DATABASE_URL is the single env var that selects the dialect.
"""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _build_engine_kwargs(url: str) -> dict:
    """Pick pool + connect args appropriate to the URL's dialect."""
    if url.startswith("sqlite"):
        return {
            "connect_args": {"check_same_thread": False},
            # SQLite's StaticPool is what we get by default for in-memory
            # and a small NullPool-like behavior for file URLs. The
            # pre-ping is harmless but unnecessary.
            "pool_pre_ping": True,
        }
    # Postgres (and anything else that talks to a real server)
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        # Postgres's default idle_in_transaction_session_timeout + cloud
        # provider idle disconnects make a 1-hour recycle the safe floor.
        "pool_recycle": 3600,
    }


engine = create_engine(
    settings.DATABASE_URL,
    future=True,
    **_build_engine_kwargs(settings.DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


# SQLite doesn't enforce foreign-key constraints unless PRAGMA
# foreign_keys is set per-connection. Without this, ON DELETE CASCADE /
# SET NULL silently no-op on SQLite dev/test runs — masking real bugs
# that would later bite under Postgres (which always enforces FKs).
# The listener fires on every checkout from the pool; harmless if it
# fires for non-SQLite engines (the isinstance check short-circuits).
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    # Only act on SQLite connections — duck-type check via the dbapi
    # module since SQLAlchemy doesn't pass dialect info to this hook.
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
