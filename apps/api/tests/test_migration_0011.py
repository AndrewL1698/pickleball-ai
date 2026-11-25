"""Migration 0011: the four players of a match.

SQLite with foreign keys on, as in `test_migration_0008.py`; `test_migrations.py`
checks PostgreSQL against the models.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from test_migration_0008 import MATCH, OTHER_MATCH, insert_match
from tests_support_api import ALEMBIC_INI

BEFORE = "0010_model_run_chunks"
THIS = "0011_players"


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        yield connection
    engine.dispose()


def migrate(connection: Connection, direction: str, revision: str) -> None:
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = connection
    getattr(command, direction)(config, revision)
    connection.commit()


def insert_player(
    connection: Connection,
    number: int,
    *,
    team: str | None = None,
    match: str = MATCH,
    name: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO players (id, match_id, team, number, name)"
            " VALUES (:id, :match, :team, :number, :name)"
        ),
        {
            "id": f"{match[-1]}{number:031x}",
            "match": match,
            "team": team or ("A" if number <= 2 else "B"),
            "number": number,
            "name": name,
        },
    )


def count(connection: Connection) -> int:
    return int(connection.execute(text("SELECT count(*) FROM players")).scalar_one())


def test_existing_matches_have_no_players_after_the_upgrade(connection: Connection) -> None:
    migrate(connection, "upgrade", BEFORE)
    insert_match(connection)
    connection.commit()
    migrate(connection, "upgrade", THIS)
    assert count(connection) == 0
    assert connection.execute(text("SELECT count(*) FROM matches")).scalar_one() == 1


def test_a_match_holds_four_numbered_players_in_two_teams(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    for number in (1, 2, 3, 4):
        insert_player(connection, number)
    insert_match(connection, OTHER_MATCH)
    insert_player(connection, 1, match=OTHER_MATCH, name="Sam")
    rows = connection.execute(
        text("SELECT number, team FROM players WHERE match_id = :m ORDER BY number"),
        {"m": MATCH},
    ).all()
    assert [tuple(row) for row in rows] == [(1, "A"), (2, "A"), (3, "B"), (4, "B")]


@pytest.mark.parametrize(
    "values",
    [
        {"number": 1, "team": "A"},  # one player 1 per match
        {"number": 0},
        {"number": 5},
        {"number": 2, "team": "B"},  # 1-2 are team A
        {"number": 3, "team": "A"},  # 3-4 are team B
        {"number": 3, "team": "C"},
        {"number": 2, "match": "0000000000000000000000000000000f"},
    ],
)
def test_an_impossible_player_is_refused_by_the_database(
    connection: Connection, values: dict[str, object]
) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    insert_player(connection, 1)
    connection.commit()
    with pytest.raises(IntegrityError):
        insert_player(connection, **values)  # type: ignore[arg-type]


def test_deleting_the_match_deletes_its_players(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    insert_player(connection, 1)
    connection.execute(text("DELETE FROM matches"))
    assert count(connection) == 0


def test_a_downgrade_drops_the_players(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    insert_player(connection, 1)
    connection.commit()

    migrate(connection, "downgrade", BEFORE)
    assert "players" not in inspect(connection).get_table_names()
    assert connection.execute(text("SELECT count(*) FROM matches")).scalar_one() == 1
