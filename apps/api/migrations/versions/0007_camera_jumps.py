"""Where the camera moved during a video, as far as sampled frames can tell.

Null means not checked, which is every video processed before this and is not
the same as an empty list, which means checked and no move found. Nothing is
backfilled, because a migration must not start work.

Revision ID: 0007_camera_jumps
Revises: 0006_court_calibrations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_camera_jumps"
down_revision: str | None = "0006_court_calibrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("camera_jumps", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("camera_jumps")
