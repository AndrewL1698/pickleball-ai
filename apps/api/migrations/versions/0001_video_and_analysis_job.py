"""The first two tables: uploaded videos and the jobs that analyze them.

The enum columns are deliberately VARCHAR plus a CHECK rather than native
PostgreSQL enums: adding a member to a native enum needs its own migration, and
the test suite runs the same migration against SQLite, which has no enum type.

The status and stage values are written out literally instead of imported from
`pickleball_api.models`. A migration records what the schema was on the day it
ran; importing the enums would silently rewrite history when they grow.

Revision ID: a8fc892f12ee
Revises: (none, this is the first)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8fc892f12ee"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATUS = sa.Enum(
    "queued", "running", "ready", "failed", name="job_status", native_enum=False
)
JOB_STAGE = sa.Enum(
    "ingested",
    "metadata_ready",
    "court_ready",
    "players_ready",
    "ball_ready",
    "rallies_ready",
    "analytics_ready",
    name="job_stage",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        # BigInteger: the default upload limit alone is 2 GiB, past INT4.
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_videos_byte_size_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_videos_created_at", "videos", ["created_at"])

    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("status", JOB_STATUS, nullable=False),
        sa.Column("stage", JOB_STAGE, nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress >= 0.0 AND progress <= 1.0", name="ck_analysis_jobs_progress"
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_jobs_video_id", "analysis_jobs", ["video_id"])
    op.create_index(
        "ix_analysis_jobs_video_id_created_at", "analysis_jobs", ["video_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_jobs_video_id_created_at", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_video_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
    op.drop_index("ix_videos_created_at", table_name="videos")
    op.drop_table("videos")
