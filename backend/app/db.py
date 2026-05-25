"""SQLAlchemy engine and session management.

Engine config is dialect-aware:

- **SQLite** (default / today): `check_same_thread=False` so the
  APScheduler thread and FastAPI request handlers can share the engine.
  Pool defaults are fine; SQLite holds one connection at a time anyway.
- **Postgres** (phase 2b onwards): explicit pool size + recycle so the
  long-running poller + Celery workers don't accumulate stale
  connections through Postgres's idle-disconnect window. `pool_pre_ping`
  catches the half-dropped-connection case before a query fails.

DATABASE_URL is the single env var that flips between them. To use
Postgres, set `DATABASE_URL=postgresql+psycopg://user:pass@host:port/db`
(the `+psycopg` qualifier explicitly selects psycopg3, which is what's
installed; plain `postgresql://` would fall back to psycopg2 if
present).
"""

from collections.abc import Generator

from sqlalchemy import create_engine
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


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
