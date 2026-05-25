"""Alembic environment.

DATABASE_URL is loaded from app.config.Settings (env-var driven) so the same
migration tree drives SQLite (today) and Postgres (phase 2b+) without
duplicating connection-string config.

Every model module is imported at the top of this file — that registers each
table on Base.metadata so autogenerate (and the eventual `alembic upgrade`
check that all tables exist) sees the full schema.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import Base

# Importing each module's models package registers every table on
# Base.metadata. Mirrors the pattern in app.main.
from app.capacity import models as _capacity_models  # noqa: F401
from app.core.auth import models as _auth_models  # noqa: F401
from app.core.devices import models as _devices_models  # noqa: F401
from app.core.panorama import models as _panorama_models  # noqa: F401


config = context.config

# Alembic's own logging config (alembic.ini)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DATABASE_URL from app.config — same value FastAPI uses.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return config.get_main_option("sqlalchemy.url", "").startswith("sqlite")


def run_migrations_offline() -> None:
    """Generate SQL to stdout without connecting to a DB. Useful for review."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        # SQLite can't ALTER most things — alembic emits create-new + copy + drop-old
        # via "batch" mode. Enable when on SQLite.
        render_as_batch=_is_sqlite(),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Batch mode for SQLite so ADD/DROP COLUMN works via copy-and-rename.
            render_as_batch=_is_sqlite(),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
