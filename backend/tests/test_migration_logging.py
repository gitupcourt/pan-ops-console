"""Guard: running migrations in-process (FastAPI lifespan path) must NOT
clobber the app's logging.

Regression context: `alembic/env.py` calls `logging.config.fileConfig()` to
set up Alembic's own logging. For the standalone `alembic` CLI that's
correct. But when migrations run in-process from the app lifespan,
fileConfig reconfigures the HOST process's root logger to alembic.ini's
settings (root level WARN, disable_existing_loggers=True) — silently
dropping every app INFO log for the life of the process: request logs,
the OIDC callback claims line, AND the F-4 crypto/login audit trail. In
prod this presented as "the backend logs nothing after startup," which
made an OIDC lockout impossible to diagnose.

The fix: app.core.migrations sets `config.attributes["configure_logging"]
= False`, and env.py skips fileConfig when that flag is set.
"""

from __future__ import annotations

import logging


def test_alembic_config_disables_logging_reconfig():
    """The in-process config must signal env.py to leave logging alone."""
    from app.core.migrations import _alembic_config

    cfg = _alembic_config()
    assert cfg.attributes.get("configure_logging") is False


def test_run_migrations_does_not_clobber_root_logger():
    """End-to-end: after the app migration path runs, the root logger must
    still emit INFO and existing app loggers must not be disabled.

    With the bug present, alembic's fileConfig bumps root to WARN and
    disables existing loggers, so this fails. With the guard, the app's
    logging is untouched.
    """
    from app.core.migrations import run_migrations

    root = logging.getLogger()
    audit = logging.getLogger("audit.crypto")  # an existing app logger (F-4)
    saved_level = root.level
    saved_disabled = audit.disabled
    try:
        root.setLevel(logging.INFO)
        audit.disabled = False

        run_migrations()  # the in-process path; loads env.py

        assert root.level <= logging.INFO, (
            "root logger level is "
            f"{logging.getLevelName(root.level)} after migrations — "
            "alembic fileConfig clobbered app logging"
        )
        assert not audit.disabled, (
            "the F-4 audit logger was disabled by alembic's "
            "disable_existing_loggers — audit trail would be dead in prod"
        )
    finally:
        root.setLevel(saved_level)
        audit.disabled = saved_disabled
