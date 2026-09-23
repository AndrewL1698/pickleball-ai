"""Constrain the status and stage columns to their known values.

Migration 0001 created these as plain VARCHAR. `Enum(native_enum=False)`
defaults to `create_constraint=False` in SQLAlchemy 2, so the CHECK the models'
docstring described was never emitted, and any string of the right length was
accepted at the SQL level.

Adding it now is defence in depth: the application already refuses a bad value
through `validate_strings`, but a migration, a psql session or a future bug
would not have been.

Revision ID: 0002_enum_checks
Revises: a8fc892f12ee
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_enum_checks"
down_revision: str | None = "a8fc892f12ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALUES = ("queued", "running", "ready", "failed")
STAGE_VALUES = (
    "ingested",
    "metadata_ready",
    "court_ready",
    "players_ready",
    "ball_ready",
    "rallies_ready",
    "analytics_ready",
)


def _in_clause(column: str, values: Sequence[str]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


def upgrade() -> None:
    # batch_alter_table: SQLite cannot ADD CONSTRAINT, and the test suite runs
    # these same migrations against SQLite.
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.create_check_constraint("job_status", _in_clause("status", STATUS_VALUES))
        batch.create_check_constraint("job_stage", _in_clause("stage", STAGE_VALUES))


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.drop_constraint("job_stage", type_="check")
        batch.drop_constraint("job_status", type_="check")
