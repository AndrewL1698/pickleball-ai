"""What kind of work each analysis job does: `preparation` or `players`.

`preparation` is the metadata, calibration images and camera check every upload
gets, which is all a job did before this, so every existing row is backfilled as
one. `players` runs player tracking once the court is calibrated. A VARCHAR plus
a CHECK, like the other enums (see 0002). The one-active-job-per-match index is
unchanged: it covers jobs of every kind.

On SQLite the column, its default and its CHECK are added in one plain
`ALTER TABLE ... ADD COLUMN`, as 0008 does for `matches`, rather than in batch
mode: rebuilding `analysis_jobs` would have to recreate the partial unique index
from reflection.

Values are written out literally rather than imported from the application,
for the reason given in 0001.

Revision ID: 0009_analysis_job_kind
Revises: 0008_model_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_analysis_job_kind"
down_revision: str | None = "0008_model_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "job_kind"
CONDITION = "kind IN ('preparation', 'players')"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE analysis_jobs ADD COLUMN kind VARCHAR(11) NOT NULL"
            f" DEFAULT 'preparation' CONSTRAINT {CHECK} CHECK ({CONDITION})"
        )
    else:
        op.add_column(
            "analysis_jobs",
            sa.Column("kind", sa.String(length=11), nullable=False, server_default="preparation"),
        )
        op.create_check_constraint(CHECK, "analysis_jobs", CONDITION)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE analysis_jobs DROP COLUMN kind")
    else:
        op.drop_constraint(CHECK, "analysis_jobs", type_="check")
        op.drop_column("analysis_jobs", "kind")
