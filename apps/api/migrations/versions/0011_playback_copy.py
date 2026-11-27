"""Where a video's playback copy is stored: an H.264 MP4 any browser can seek through.

Null means not made yet, which is every video processed before this, and also a
video whose transcode failed. Nothing is backfilled, because a migration must
not start work; `pbworker-backfill-playback` makes the missing copies.

A copy belongs to one video, so its key is unique, like `storage_key`.

Revision ID: 0011_playback_copy
Revises: 0010_model_run_chunks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_playback_copy"
down_revision: str | None = "0010_model_run_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("playback_storage_key", sa.String(length=512), nullable=True))
        batch.create_unique_constraint(
            "uq_videos_playback_storage_key", ["playback_storage_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.drop_constraint("uq_videos_playback_storage_key", type_="unique")
        batch.drop_column("playback_storage_key")
