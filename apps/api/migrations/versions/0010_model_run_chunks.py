"""The finished chunks of a Detect run.

Detect over a whole match takes about an hour, so it saves its output in chunks
of video (five minutes by default), each a Parquet object in storage named by one
row here. A retry reads the rows to find the first unfinished chunk. A Detect
run's output is its chunk set, so its `artifact_storage_key` stays null.

A chunk is written once: `(run_id, index)` is unique, and so is the object's key.
Deleting a run deletes its chunk rows; their objects stay in storage until
something removes them, as for a downgrade.

Revision ID: 0010_model_run_chunks
Revises: 0009_analysis_job_kind
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_model_run_chunks"
down_revision: str | None = "0009_analysis_job_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_run_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"index" >= 0', name="ck_model_run_chunks_index_non_negative"),
        sa.CheckConstraint(
            "end_seconds >= start_seconds", name="ck_model_run_chunks_end_after_start"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["model_runs.id"],
            name="fk_model_run_chunks_run_id_model_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "index", name="uq_model_run_chunks_run_id_index"),
        sa.UniqueConstraint("storage_key"),
    )


def downgrade() -> None:
    op.drop_table("model_run_chunks")
