"""Introduce `matches` as the owner of videos and analysis jobs.

Phase 1 stored a `Video` and hung its `AnalysisJob`s off it. From Phase 2 a
`Match` is the parent: it owns one `Video` (`videos.match_id`, unique) and every
job (`analysis_jobs.match_id`, replacing `video_id`), and later owns the court
calibration, players and rallies.

Existing rows are kept. Each Phase 1 video becomes a match of its own:

- The match reuses the video's id. Both are random UUIDs, the two live in
  different tables, and doing so means a link a user saved to `/videos/<id>`
  can be redirected to `/matches/<id>` and still find the same upload.
- Its name is the upload's filename without the extension, `created_at` is the
  video's, and `recorded_at` is unknown.
- Its status follows its latest job: queued -> uploaded, running -> processing,
  ready -> calibration_required, failed -> failed. Nothing is calibrated yet,
  so nothing becomes `court_ready`.

The order of operations matters on SQLite, where the test suite runs this with
foreign keys enforced. `batch_alter_table` rebuilds a table by creating a copy
and dropping the original, and dropping a table with foreign keys on performs an
implicit DELETE, which fires `ON DELETE CASCADE` on anything still pointing at
it. So a table is only ever rebuilt when nothing references it: `analysis_jobs`
stops referencing `videos` before `videos` is rebuilt, and in the downgrade the
reference is restored only after `videos` has been rebuilt.

Values are written out literally rather than imported from the application,
for the reason given in 0001: a migration records the schema on the day it ran.

Revision ID: 0003_match_ownership
Revises: 0002_enum_checks
"""

from collections.abc import Sequence
from pathlib import PurePosixPath

import sqlalchemy as sa
from alembic import op

revision: str = "0003_match_ownership"
down_revision: str | None = "0002_enum_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATCH_STATUS_VALUES = ("uploaded", "processing", "calibration_required", "court_ready", "failed")

#: A Phase 1 video's latest job status -> the status its new match starts in.
MATCH_STATUS_FOR_JOB = {
    "queued": "uploaded",
    "running": "processing",
    "ready": "calibration_required",
    "failed": "failed",
}

MAX_NAME_CHARS = 200
UNNAMED = "Untitled match"

matches = sa.table(
    "matches",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("recorded_at", sa.DateTime(timezone=True)),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
videos = sa.table(
    "videos",
    sa.column("id", sa.Uuid()),
    sa.column("match_id", sa.Uuid()),
    sa.column("original_filename", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
jobs = sa.table(
    "analysis_jobs",
    sa.column("id", sa.Uuid()),
    sa.column("video_id", sa.Uuid()),
    sa.column("match_id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _name_from_filename(filename: str) -> str:
    """The same rule as `pickleball_api.uploads.default_match_name`, frozen here."""
    name = " ".join(PurePosixPath(filename).stem.split())[:MAX_NAME_CHARS].strip()
    return name or UNNAMED


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=MAX_NAME_CHARS), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*MATCH_STATUS_VALUES, name="match_status", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{v}'" for v in MATCH_STATUS_VALUES)),
            name="match_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_matches_created_at", "matches", ["created_at"])

    # Nullable for now: they are filled in below, then tightened.
    op.add_column("videos", sa.Column("match_id", sa.Uuid(), nullable=True))
    op.add_column("analysis_jobs", sa.Column("match_id", sa.Uuid(), nullable=True))

    _backfill_matches()

    # analysis_jobs first, so that by the time `videos` is rebuilt nothing
    # references it (see the module docstring).
    op.drop_index("ix_analysis_jobs_video_id_created_at", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_video_id", table_name="analysis_jobs")
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.alter_column("match_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_analysis_jobs_match_id_matches",
            "matches",
            ["match_id"],
            ["id"],
            ondelete="CASCADE",
        )
        # Dropping the column drops its foreign key with it on PostgreSQL, and
        # the rebuilt table simply omits it on SQLite.
        batch.drop_column("video_id")
    op.create_index(
        "ix_analysis_jobs_match_id_created_at", "analysis_jobs", ["match_id", "created_at"]
    )

    with op.batch_alter_table("videos") as batch:
        batch.alter_column("match_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_unique_constraint("uq_videos_match_id", ["match_id"])
        batch.create_foreign_key(
            "fk_videos_match_id_matches", "matches", ["match_id"], ["id"], ondelete="CASCADE"
        )


def _backfill_matches() -> None:
    connection = op.get_bind()
    latest_status: dict[object, str] = {}
    # Ascending, so the last write per video is its latest job. Ties on the
    # timestamp are broken by id, exactly as the application orders them.
    for video_id, status in connection.execute(
        sa.select(jobs.c.video_id, jobs.c.status).order_by(jobs.c.created_at, jobs.c.id)
    ):
        latest_status[video_id] = status

    rows = connection.execute(
        sa.select(videos.c.id, videos.c.original_filename, videos.c.created_at)
    ).all()
    for video_id, filename, created_at in rows:
        connection.execute(
            matches.insert().values(
                id=video_id,
                name=_name_from_filename(filename or ""),
                recorded_at=None,
                # A video with no job at all was never picked up: `uploaded`.
                status=MATCH_STATUS_FOR_JOB.get(latest_status.get(video_id, ""), "uploaded"),
                created_at=created_at,
            )
        )
    # The match id equals the video id, so both columns are one statement each.
    connection.execute(videos.update().values(match_id=videos.c.id))
    connection.execute(jobs.update().values(match_id=jobs.c.video_id))


def downgrade() -> None:
    """Return to Phase 1: jobs belong to videos, and matches are dropped.

    What cannot be carried back is lost: a match's name, `recorded_at` and
    status have no Phase 1 column. A job whose match has no video has no Phase 1
    representation either, so the downgrade refuses rather than deleting it.
    """
    connection = op.get_bind()
    orphaned = connection.execute(
        sa.select(sa.func.count())
        .select_from(jobs.outerjoin(videos, videos.c.match_id == jobs.c.match_id))
        .where(videos.c.id.is_(None))
    ).scalar_one()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} analysis job(s) belong to a match with no video and cannot be "
            "represented before revision 0003; resolve them before downgrading."
        )

    op.add_column("analysis_jobs", sa.Column("video_id", sa.Uuid(), nullable=True))
    connection.execute(
        jobs.update().values(
            video_id=sa.select(videos.c.id)
            .where(videos.c.match_id == jobs.c.match_id)
            .scalar_subquery()
        )
    )

    # `videos` is rebuilt first, while analysis_jobs does not reference it yet.
    with op.batch_alter_table("videos") as batch:
        batch.drop_constraint("fk_videos_match_id_matches", type_="foreignkey")
        batch.drop_constraint("uq_videos_match_id", type_="unique")
        batch.drop_column("match_id")

    op.drop_index("ix_analysis_jobs_match_id_created_at", table_name="analysis_jobs")
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.alter_column("video_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "analysis_jobs_video_id_fkey", "videos", ["video_id"], ["id"], ondelete="CASCADE"
        )
        batch.drop_constraint("fk_analysis_jobs_match_id_matches", type_="foreignkey")
        batch.drop_column("match_id")
    op.create_index("ix_analysis_jobs_video_id", "analysis_jobs", ["video_id"])
    op.create_index(
        "ix_analysis_jobs_video_id_created_at", "analysis_jobs", ["video_id", "created_at"]
    )

    op.drop_index("ix_matches_created_at", table_name="matches")
    op.drop_table("matches")
