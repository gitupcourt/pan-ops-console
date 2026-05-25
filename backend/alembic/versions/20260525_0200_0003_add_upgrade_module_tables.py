"""add upgrade module tables

Phase 4c-models. Additive — creates the eight tables the upgrade module
needs (UpgradeJob, DeviceUpgradeTask, Snapshot, SnapshotDiff, PrecheckRun,
BulkPrecheckRun, PrecheckSet, PanosImage, BulkStageRun, DeviceStageRun)
plus their Postgres enum types (workflow_type, job_state, task_phase,
severity, snapshot_kind).

Capacity polling sees none of this; the new tables and types are inert
until phase 4c-services moves the orchestrator + classifier + service
code in, and phase 4c-routes adds the HTTP surface.

FK cascade choices follow MIGRATION_NOTES §8:
- snapshots.device_id        ON DELETE CASCADE   (no orphan snapshots)
- snapshots.task_id          ON DELETE SET NULL  (snapshots outlive tasks)
- snapshot_diffs.left/right  ON DELETE CASCADE   (diff is meaningless without both)
- snapshot_diffs.task_id     ON DELETE SET NULL  (diff outlives task)
- device_upgrade_tasks.{job,device}_id   default no-action

Indexes preserved:
- device_upgrade_tasks.job_id, .device_id, .ha_pair_key
- snapshots.device_id, .task_id, .taken_at
- snapshot_diffs.left/right_snapshot_id, .task_id
- precheck_runs.device_id, .ran_at, .bulk_run_id
- device_stage_runs.bulk_run_id, .device_id, .started_at
- panos_images.version

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25 02:00:00 UTC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- Enum types (Postgres-native; SQLite uses VARCHAR+CHECK) ----------
    # On Postgres, the underlying ENUM types are created up front via raw
    # SQL so multiple CREATE TABLEs that reference the same type don't
    # each try to CREATE TYPE (which raises DuplicateObject). The
    # column-level `sa.Enum(..., create_type=False)` then references the
    # already-existing type.
    #
    # On SQLite, no native enum exists — the column-level Enum emits a
    # VARCHAR + CHECK constraint per column, no type pre-creation needed,
    # so this block is skipped entirely.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Idempotent CREATE TYPE via DO/EXCEPTION — Postgres has no
        # native "CREATE TYPE IF NOT EXISTS." This both handles re-runs
        # cleanly and avoids races with whatever else (SQLAlchemy
        # column-level auto-create, partial-rollback artifacts) might
        # have already created the type.
        def _create_enum(name: str, values: tuple[str, ...]) -> None:
            vals = ", ".join(f"'{v}'" for v in values)
            op.execute(
                f"DO $$ BEGIN "
                f"CREATE TYPE {name} AS ENUM ({vals}); "
                f"EXCEPTION WHEN duplicate_object THEN null; "
                f"END $$;"
            )

        _create_enum("workflow_type", ("full", "partial"))
        _create_enum(
            "job_state",
            (
                "pending", "running", "awaiting_confirmation",
                "completed", "failed", "aborted",
            ),
        )
        _create_enum(
            "task_phase",
            (
                "pending", "precheck", "awaiting_precheck_override",
                "snapshot", "downloading_image",
                "suspend_secondary", "upgrade_secondary",
                "awaiting_reboot_confirm",
                "postcheck_secondary", "awaiting_postcheck_override",
                "awaiting_failover_confirm", "failover",
                "awaiting_primary_upgrade_confirm", "upgrade_primary",
                "postcheck_primary", "failback", "report", "done", "failed",
            ),
        )
        _create_enum("severity", ("pass", "warn", "fail", "skip"))
        _create_enum(
            "snapshot_kind", ("pre_upgrade", "post_upgrade", "ad_hoc")
        )

    # Column-level Enums. On Postgres we use the dialect-specific
    # `postgresql.ENUM(..., create_type=False)` which reliably suppresses
    # auto-create (sa.Enum's create_type=False didn't, possibly because
    # Base.metadata's existing model-level Enum with the same name
    # shadows the local one inside alembic's table render path). On
    # SQLite we use sa.Enum so the VARCHAR+CHECK emulation kicks in.
    is_pg = bind.dialect.name == "postgresql"

    def _enum(name: str, values: tuple[str, ...]):
        if is_pg:
            return postgresql.ENUM(*values, name=name, create_type=False)
        return sa.Enum(*values, name=name)

    _wf = _enum("workflow_type", ("full", "partial"))
    _js = _enum(
        "job_state",
        (
            "pending", "running", "awaiting_confirmation",
            "completed", "failed", "aborted",
        ),
    )
    _tp = _enum(
        "task_phase",
        (
            "pending", "precheck", "awaiting_precheck_override",
            "snapshot", "downloading_image",
            "suspend_secondary", "upgrade_secondary",
            "awaiting_reboot_confirm",
            "postcheck_secondary", "awaiting_postcheck_override",
            "awaiting_failover_confirm", "failover",
            "awaiting_primary_upgrade_confirm", "upgrade_primary",
            "postcheck_primary", "failback", "report", "done", "failed",
        ),
    )
    _sev = _enum("severity", ("pass", "warn", "fail", "skip"))
    _sk = _enum(
        "snapshot_kind", ("pre_upgrade", "post_upgrade", "ad_hoc")
    )

    # ---------- panos_images ----------
    op.create_table(
        "panos_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_panos_images_version", "panos_images", ["version"])

    # ---------- upgrade_jobs ----------
    op.create_table(
        "upgrade_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_version", sa.String(length=64), nullable=False),
        sa.Column(
            "workflow", _wf, nullable=False, server_default="full",
        ),
        sa.Column("workflow_stages", sa.JSON(), nullable=True),
        sa.Column(
            "require_failover_confirmation", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "require_primary_upgrade_confirmation", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "auto_failback", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "auto_reboot_after_install", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "auto_ack_precheck_failures", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "auto_ack_postcheck_failures", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column("image_id", sa.Integer(), nullable=True),
        sa.Column(
            "device_pull_image", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "state", _js, nullable=False, server_default="pending",
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["image_id"], ["panos_images.id"],
            name="fk_upgrade_jobs_image_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"],
            name="fk_upgrade_jobs_created_by_id",
        ),
    )

    # ---------- device_upgrade_tasks ----------
    op.create_table(
        "device_upgrade_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("ha_pair_key", sa.String(length=64), nullable=False),
        sa.Column(
            "phase", _tp, nullable=False, server_default="pending",
        ),
        sa.Column("progress", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("confirmation_token", sa.String(length=64), nullable=True),
        sa.Column(
            "tick_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["upgrade_jobs.id"],
            name="fk_device_upgrade_tasks_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            name="fk_device_upgrade_tasks_device_id",
        ),
    )
    op.create_index(
        "ix_device_upgrade_tasks_job_id", "device_upgrade_tasks", ["job_id"]
    )
    op.create_index(
        "ix_device_upgrade_tasks_device_id", "device_upgrade_tasks", ["device_id"]
    )
    op.create_index(
        "ix_device_upgrade_tasks_ha_pair_key",
        "device_upgrade_tasks", ["ha_pair_key"],
    )

    # ---------- snapshots ----------
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("kind", _sk, nullable=False),
        sa.Column(
            "taken_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("pan_os_version", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            ondelete="CASCADE", name="fk_snapshots_device_id",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["device_upgrade_tasks.id"],
            ondelete="SET NULL", name="fk_snapshots_task_id",
        ),
    )
    op.create_index("ix_snapshots_device_id", "snapshots", ["device_id"])
    op.create_index("ix_snapshots_task_id", "snapshots", ["task_id"])
    op.create_index("ix_snapshots_taken_at", "snapshots", ["taken_at"])

    # ---------- snapshot_diffs ----------
    op.create_table(
        "snapshot_diffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("left_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("right_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "all_passed", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column("failing_areas", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["left_snapshot_id"], ["snapshots.id"],
            ondelete="CASCADE", name="fk_snapshot_diffs_left_snapshot_id",
        ),
        sa.ForeignKeyConstraint(
            ["right_snapshot_id"], ["snapshots.id"],
            ondelete="CASCADE", name="fk_snapshot_diffs_right_snapshot_id",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["device_upgrade_tasks.id"],
            ondelete="SET NULL", name="fk_snapshot_diffs_task_id",
        ),
    )
    op.create_index(
        "ix_snapshot_diffs_left_snapshot_id",
        "snapshot_diffs", ["left_snapshot_id"],
    )
    op.create_index(
        "ix_snapshot_diffs_right_snapshot_id",
        "snapshot_diffs", ["right_snapshot_id"],
    )
    op.create_index(
        "ix_snapshot_diffs_task_id", "snapshot_diffs", ["task_id"]
    )

    # ---------- bulk_precheck_runs ----------
    op.create_table(
        "bulk_precheck_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("checks_requested", sa.JSON(), nullable=False),
        sa.Column(
            "cancelled", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"], ["users.id"],
            name="fk_bulk_precheck_runs_user_id",
        ),
    )

    # ---------- precheck_runs ----------
    op.create_table(
        "precheck_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("bulk_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("ran_by_user_id", sa.Integer(), nullable=True),
        sa.Column("overall_severity", _sev, nullable=False),
        sa.Column(
            "pass_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "warn_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "fail_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "skip_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            ondelete="CASCADE", name="fk_precheck_runs_device_id",
        ),
        sa.ForeignKeyConstraint(
            ["bulk_run_id"], ["bulk_precheck_runs.id"],
            ondelete="SET NULL", name="fk_precheck_runs_bulk_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["ran_by_user_id"], ["users.id"],
            name="fk_precheck_runs_user_id",
        ),
    )
    op.create_index("ix_precheck_runs_device_id", "precheck_runs", ["device_id"])
    op.create_index("ix_precheck_runs_bulk_run_id", "precheck_runs", ["bulk_run_id"])
    op.create_index("ix_precheck_runs_ran_at", "precheck_runs", ["ran_at"])

    # ---------- precheck_sets ----------
    op.create_table(
        "precheck_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_precheck_sets_user_id",
        ),
        sa.UniqueConstraint("name", name="uq_precheck_sets_name"),
    )

    # ---------- bulk_stage_runs ----------
    op.create_table(
        "bulk_stage_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "cancelled", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"], ["users.id"],
            name="fk_bulk_stage_runs_user_id",
        ),
    )

    # ---------- device_stage_runs ----------
    op.create_table(
        "device_stage_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bulk_run_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome", _sev, nullable=False, server_default="fail",
        ),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["bulk_run_id"], ["bulk_stage_runs.id"],
            ondelete="SET NULL", name="fk_device_stage_runs_bulk_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"],
            ondelete="CASCADE", name="fk_device_stage_runs_device_id",
        ),
    )
    op.create_index(
        "ix_device_stage_runs_bulk_run_id", "device_stage_runs", ["bulk_run_id"]
    )
    op.create_index(
        "ix_device_stage_runs_device_id", "device_stage_runs", ["device_id"]
    )
    op.create_index(
        "ix_device_stage_runs_started_at", "device_stage_runs", ["started_at"]
    )


def downgrade() -> None:
    # Drop tables in reverse FK-dependency order.
    op.drop_index("ix_device_stage_runs_started_at", table_name="device_stage_runs")
    op.drop_index("ix_device_stage_runs_device_id", table_name="device_stage_runs")
    op.drop_index("ix_device_stage_runs_bulk_run_id", table_name="device_stage_runs")
    op.drop_table("device_stage_runs")
    op.drop_table("bulk_stage_runs")

    op.drop_table("precheck_sets")

    op.drop_index("ix_precheck_runs_ran_at", table_name="precheck_runs")
    op.drop_index("ix_precheck_runs_bulk_run_id", table_name="precheck_runs")
    op.drop_index("ix_precheck_runs_device_id", table_name="precheck_runs")
    op.drop_table("precheck_runs")
    op.drop_table("bulk_precheck_runs")

    op.drop_index("ix_snapshot_diffs_task_id", table_name="snapshot_diffs")
    op.drop_index("ix_snapshot_diffs_right_snapshot_id", table_name="snapshot_diffs")
    op.drop_index("ix_snapshot_diffs_left_snapshot_id", table_name="snapshot_diffs")
    op.drop_table("snapshot_diffs")

    op.drop_index("ix_snapshots_taken_at", table_name="snapshots")
    op.drop_index("ix_snapshots_task_id", table_name="snapshots")
    op.drop_index("ix_snapshots_device_id", table_name="snapshots")
    op.drop_table("snapshots")

    op.drop_index("ix_device_upgrade_tasks_ha_pair_key", table_name="device_upgrade_tasks")
    op.drop_index("ix_device_upgrade_tasks_device_id", table_name="device_upgrade_tasks")
    op.drop_index("ix_device_upgrade_tasks_job_id", table_name="device_upgrade_tasks")
    op.drop_table("device_upgrade_tasks")
    op.drop_table("upgrade_jobs")

    op.drop_index("ix_panos_images_version", table_name="panos_images")
    op.drop_table("panos_images")

    # Postgres-native types — drop in reverse-create order. No-op on SQLite.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS snapshot_kind")
        op.execute("DROP TYPE IF EXISTS severity")
        op.execute("DROP TYPE IF EXISTS task_phase")
        op.execute("DROP TYPE IF EXISTS job_state")
        op.execute("DROP TYPE IF EXISTS workflow_type")
