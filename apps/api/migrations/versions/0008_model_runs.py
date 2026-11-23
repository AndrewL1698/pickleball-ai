"""Model runs, and the Identify run each match is showing.

A run is one Detect or Identify pass over a match (ADR 0001). Its output is a
Parquet artifact in storage, and the row says which model and configuration made
it. Runs are only ever added. `matches.current_identify_run_id` is the run the
match shows, and it starts null for every existing match: nothing has been
tracked yet, and a migration must not start work.

`matches` gains a foreign key, which SQLite cannot add with ALTER, and batch
mode would rebuild the table. With foreign keys on, dropping the old `matches`
in that rebuild cascades into every row the match owns, so on SQLite the column
and its constraint are added in one plain `ALTER TABLE ... ADD COLUMN`, which
SQLite does allow for a nullable reference.

The downgrade forgets every run. Their artifacts stay in storage until something
removes them.

Values are written out literally rather than imported from the application,
for the reason given in 0001.

Revision ID: 0008_model_runs
Revises: 0007_camera_jumps
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_model_runs"
down_revision: str | None = "0007_camera_jumps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_RUN_FK = "fk_matches_current_identify_run_id_model_runs"


def upgrade() -> None:
    op.create_table(
        "model_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=8), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_storage_key", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("stage IN ('detect', 'identify')", name="model_run_stage"),
        sa.CheckConstraint(
            "(stage = 'detect' AND parent_run_id IS NULL)"
            " OR (stage = 'identify' AND parent_run_id IS NOT NULL)",
            name="ck_model_runs_parent_matches_stage",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_model_runs_completed_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.id"], name="fk_model_runs_match_id_matches", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["model_runs.id"], name="fk_model_runs_parent_run_id_model_runs"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_storage_key"),
    )
    op.create_index("ix_model_runs_match_id_started_at", "model_runs", ["match_id", "started_at"])

    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE matches ADD COLUMN current_identify_run_id CHAR(32)"
            f" CONSTRAINT {CURRENT_RUN_FK} REFERENCES model_runs (id)"
        )
    else:
        op.add_column("matches", sa.Column("current_identify_run_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            CURRENT_RUN_FK, "matches", "model_runs", ["current_identify_run_id"], ["id"]
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # SQLite drops the column's inline constraint with it, and DROP COLUMN
        # does not rebuild the table.
        op.execute("ALTER TABLE matches DROP COLUMN current_identify_run_id")
    else:
        op.drop_constraint(CURRENT_RUN_FK, "matches", type_="foreignkey")
        op.drop_column("matches", "current_identify_run_id")
    op.drop_index("ix_model_runs_match_id_started_at", table_name="model_runs")
    op.drop_table("model_runs")
