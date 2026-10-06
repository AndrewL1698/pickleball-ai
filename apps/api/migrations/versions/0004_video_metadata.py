"""Decoded video metadata, and at most one active job per match.

The metadata columns are nullable because no row that exists at this revision
has ever been decoded: Phase 1's processor only fingerprinted the file. The
worker fills them all at once, in the same transaction as the job's `ready`
transition, and a CHECK constraint refuses a half-written record.

Matches backfilled by 0003 as `calibration_required` are moved back to
`uploaded`: that status now promises decoded metadata, which none of them has.
Nothing is enqueued here -- a migration must not start work -- so they stay
`uploaded` until someone asks for extraction through the API. The downgrade
leaves them `uploaded`, which is a valid status at 0003 too.

The partial unique index allows one `queued` or `running` job per match. It is
the database's guarantee behind the retry endpoint's own check, so two requests
racing past that check cannot both commit. Every existing match has exactly one
job, so it cannot conflict with Phase 1 data.

Values are written out literally rather than imported from the application,
for the reason given in 0001.

Revision ID: 0004_video_metadata
Revises: 0003_match_ownership
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0004_video_metadata"
down_revision: str | None = "0003_match_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_JOB_PREDICATE = "status IN ('queued', 'running')"

def _columns() -> tuple[sa.Column[Any], ...]:
    """Fresh Column objects each call: a Column can belong to only one table."""
    return (
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rotation_degrees", sa.Integer(), nullable=True),
        sa.Column("average_fps", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("frame_count", sa.Integer(), nullable=True),
        sa.Column("codec", sa.String(length=32), nullable=True),
        sa.Column("metadata_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )


CHECKS = {
    "ck_videos_width_positive": "width IS NULL OR width > 0",
    "ck_videos_height_positive": "height IS NULL OR height > 0",
    "ck_videos_average_fps_positive": "average_fps IS NULL OR average_fps > 0",
    "ck_videos_duration_non_negative": "duration_seconds IS NULL OR duration_seconds >= 0",
    "ck_videos_frame_count_non_negative": "frame_count IS NULL OR frame_count >= 0",
    "ck_videos_rotation_right_angle": (
        "rotation_degrees IS NULL OR rotation_degrees IN (0, 90, 180, 270)"
    ),
    "ck_videos_metadata_complete": (
        "(metadata_extracted_at IS NULL AND width IS NULL AND height IS NULL"
        " AND rotation_degrees IS NULL AND average_fps IS NULL"
        " AND duration_seconds IS NULL AND frame_count IS NULL AND codec IS NULL)"
        " OR (metadata_extracted_at IS NOT NULL AND width IS NOT NULL"
        " AND height IS NOT NULL AND rotation_degrees IS NOT NULL"
        " AND average_fps IS NOT NULL AND duration_seconds IS NOT NULL"
        " AND frame_count IS NOT NULL)"
    ),
}


def upgrade() -> None:
    # Nothing references `videos` since 0003 moved jobs onto matches, so the
    # SQLite rebuild batch mode performs here cannot cascade into other rows.
    with op.batch_alter_table("videos") as batch:
        for column in _columns():
            batch.add_column(column)
        for name, condition in CHECKS.items():
            batch.create_check_constraint(name, condition)

    op.create_index(
        "ux_analysis_jobs_one_active_per_match",
        "analysis_jobs",
        ["match_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_JOB_PREDICATE),
        sqlite_where=sa.text(ACTIVE_JOB_PREDICATE),
    )

    op.execute(
        "UPDATE matches SET status = 'uploaded' WHERE status = 'calibration_required'"
    )


def downgrade() -> None:
    op.drop_index("ux_analysis_jobs_one_active_per_match", table_name="analysis_jobs")
    with op.batch_alter_table("videos") as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        for column in reversed(_columns()):
            batch.drop_column(column.name)
