"""Calibration images: stills from a match's video to place landmarks on.

Nothing is backfilled and nothing is enqueued, because a migration must not start
work. A match with metadata and no images shows an action that queues the job.

Values are written out literally rather than imported from the application,
for the reason given in 0001.

Revision ID: 0005_reference_frames
Revises: 0004_video_metadata
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_reference_frames"
down_revision: str | None = "0004_video_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_frames",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=True),
        sa.Column("source_frame_count", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('background', 'sample')", name="reference_frame_kind"),
        sa.CheckConstraint("width > 0", name="ck_reference_frames_width_positive"),
        sa.CheckConstraint("height > 0", name="ck_reference_frames_height_positive"),
        sa.CheckConstraint("byte_size >= 0", name="ck_reference_frames_byte_size_non_negative"),
        sa.CheckConstraint(
            "source_frame_count > 0", name="ck_reference_frames_source_frame_count_positive"
        ),
        sa.CheckConstraint(
            "(kind = 'background' AND position_seconds IS NULL)"
            " OR (kind = 'sample' AND position_seconds IS NOT NULL)",
            name="ck_reference_frames_position_matches_kind",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_reference_frames_match_id_matches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "ordinal", name="uq_reference_frames_match_id_ordinal"),
        sa.UniqueConstraint("storage_key"),
    )


def downgrade() -> None:
    op.drop_table("reference_frames")
