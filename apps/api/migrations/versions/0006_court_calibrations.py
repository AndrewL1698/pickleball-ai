"""Court calibrations: landmarks on a reference frame and the fitted homography.

Rows are only ever added: a recalibration is a new row, and the newest is in
force. The downgrade loses them, and moves every `court_ready` match back to
`calibration_required`, because that status promises a calibration exists.

Values are written out literally rather than imported from the application,
for the reason given in 0001.

Revision ID: 0006_court_calibrations
Revises: 0005_reference_frames
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_court_calibrations"
down_revision: str | None = "0005_reference_frames"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "court_calibrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("reference_frame_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=6), nullable=False),
        sa.Column("image_points", sa.JSON(), nullable=False),
        sa.Column("court_points", sa.JSON(), nullable=False),
        sa.Column("homography", sa.JSON(), nullable=False),
        sa.Column("reprojection_error_ft", sa.JSON(), nullable=False),
        sa.Column("mean_reprojection_error_ft", sa.Float(), nullable=False),
        sa.Column("max_reprojection_error_ft", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source IN ('manual')", name="calibration_source"),
        sa.CheckConstraint(
            "mean_reprojection_error_ft >= 0", name="ck_court_calibrations_mean_error_non_negative"
        ),
        sa.CheckConstraint(
            "max_reprojection_error_ft >= 0", name="ck_court_calibrations_max_error_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_court_calibrations_match_id_matches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reference_frame_id"],
            ["reference_frames.id"],
            name="fk_court_calibrations_reference_frame_id_reference_frames",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_court_calibrations_match_id_created_at",
        "court_calibrations",
        ["match_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_court_calibrations_match_id_created_at", table_name="court_calibrations")
    op.drop_table("court_calibrations")
    op.execute("UPDATE matches SET status = 'calibration_required' WHERE status = 'court_ready'")
