"""The four players of a match.

The first Identify run of a match creates its players 1-4: 1 and 2 are team A
(the near team when the match starts), 3 and 4 team B. Later runs reuse them.
Player tracks name a player by `number`, not by id, so the ML package never
sees a database id. Side and slot are per frame and stay in the tracks.

Revision ID: 0011_players
Revises: 0010_model_run_chunks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_players"
down_revision: str | None = "0010_model_run_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("team", sa.String(length=1), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.CheckConstraint("team IN ('A', 'B')", name="player_team"),
        sa.CheckConstraint("number BETWEEN 1 AND 4", name="ck_players_number_range"),
        sa.CheckConstraint(
            "(number <= 2 AND team = 'A') OR (number >= 3 AND team = 'B')",
            name="ck_players_number_matches_team",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.id"], name="fk_players_match_id_matches", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "number", name="uq_players_match_id_number"),
    )


def downgrade() -> None:
    op.drop_table("players")
