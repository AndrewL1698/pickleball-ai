"""Migration 0008: model runs, and the Identify run a match is showing.

SQLite with foreign keys on, as in `test_migration_0006.py`; `test_migrations.py`
checks PostgreSQL against the models.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from tests_support_api import ALEMBIC_INI

BEFORE = "0007_camera_jumps"
MATCH = "00000000000000000000000000000001"
OTHER_MATCH = "00000000000000000000000000000002"
DETECT = "0000000000000000000000000000000d"
IDENTIFY = "0000000000000000000000000000000e"


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


def insert_match(connection: Connection, identifier: str = MATCH) -> None:
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at)"
            " VALUES (:id, 'match', 'court_ready', CURRENT_TIMESTAMP)"
        ),
        {"id": identifier},
    )


def insert_video(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO videos (id, match_id, original_filename, storage_key, content_type,"
            " byte_size) VALUES ('00000000000000000000000000000004', :match, 'm.mp4', 'k.mp4',"
            " 'video/mp4', 10)"
        ),
        {"match": MATCH},
    )


def insert_run(
    connection: Connection,
    *,
    identifier: str = DETECT,
    stage: str = "detect",
    parent: str | None = None,
    started_at: str = "2026-11-22 10:00:00",
    completed_at: str | None = "2026-11-22 11:00:00",
) -> None:
    connection.execute(
        text(
            "INSERT INTO model_runs (id, match_id, stage, model_version, config_json,"
            " config_hash, artifact_storage_key, started_at, completed_at, parent_run_id)"
            " VALUES (:id, :match, :stage, 'yolo11m', '{}', 'abc', :key, :started, :completed,"
            " :parent)"
        ),
        {
            "id": identifier,
            "match": MATCH,
            "stage": stage,
            "key": f"{identifier}.parquet",
            "started": started_at,
            "completed": completed_at,
            "parent": parent,
        },
    )


def insert_detect_and_identify(connection: Connection) -> None:
    insert_run(connection)
    insert_run(connection, identifier=IDENTIFY, stage="identify", parent=DETECT)


def show(connection: Connection, run: str | None, match: str = MATCH) -> None:
    connection.execute(
        text("UPDATE matches SET current_identify_run_id = :run WHERE id = :match"),
        {"run": run, "match": match},
    )


def count(connection: Connection, table: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_an_existing_match_shows_no_run_after_the_upgrade(connection: Connection) -> None:
    migrate(connection, "upgrade", BEFORE)
    insert_match(connection)
    insert_video(connection)
    connection.commit()

    migrate(connection, "upgrade", "head")
    assert (
        connection.execute(text("SELECT current_identify_run_id FROM matches")).scalar_one() is None
    )
    assert count(connection, "model_runs") == 0
    # Adding the column must not rebuild `matches`: on SQLite that drop would
    # cascade to everything the match owns.
    assert count(connection, "videos") == 1


def test_a_match_can_show_an_identify_run_that_read_a_detect_run(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_detect_and_identify(connection)
    show(connection, IDENTIFY)
    assert (
        connection.execute(text("SELECT current_identify_run_id FROM matches")).scalar_one()
        == IDENTIFY
    )


def test_deleting_a_match_deletes_its_runs(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_detect_and_identify(connection)
    show(connection, IDENTIFY)

    connection.execute(text("DELETE FROM matches"))
    assert count(connection, "model_runs") == 0


def test_a_run_survives_the_deletion_of_another_match(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_match(connection, OTHER_MATCH)
    insert_run(connection)

    connection.execute(text("DELETE FROM matches WHERE id = :id"), {"id": OTHER_MATCH})
    assert count(connection, "model_runs") == 1


def test_a_run_that_is_shown_or_was_read_cannot_be_deleted_on_its_own(
    connection: Connection,
) -> None:
    """Runs are kept, so the original prediction survives corrections."""
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_detect_and_identify(connection)
    show(connection, IDENTIFY)
    with pytest.raises(IntegrityError):
        connection.execute(text("DELETE FROM model_runs WHERE id = :id"), {"id": DETECT})
    with pytest.raises(IntegrityError):
        connection.execute(text("DELETE FROM model_runs WHERE id = :id"), {"id": IDENTIFY})


def test_a_match_cannot_show_a_run_that_does_not_exist(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    with pytest.raises(IntegrityError):
        show(connection, IDENTIFY)


@pytest.mark.parametrize(
    "values",
    [
        {"stage": "pose"},
        # Detect reads the video, never another run...
        {"stage": "detect", "parent": DETECT},
        # ...and Identify always reads a Detect run.
        {"stage": "identify", "parent": None},
        {"parent": "0000000000000000000000000000000f", "stage": "identify"},
        {"started_at": "2026-11-22 12:00:00", "completed_at": "2026-11-22 11:00:00"},
    ],
)
def test_an_impossible_run_is_refused_by_the_database(
    connection: Connection, values: dict[str, str | None]
) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_run(connection)
    with pytest.raises(IntegrityError):
        insert_run(connection, identifier=IDENTIFY, **values)  # type: ignore[arg-type]


def test_an_unfinished_run_has_no_completion_time(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_run(connection, completed_at=None)
    assert count(connection, "model_runs") == 1


def test_a_downgrade_forgets_the_runs_and_keeps_the_match(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_video(connection)
    insert_detect_and_identify(connection)
    show(connection, IDENTIFY)
    connection.commit()

    migrate(connection, "downgrade", BEFORE)
    assert "model_runs" not in inspect(connection).get_table_names()
    columns = {c["name"] for c in inspect(connection).get_columns("matches")}
    assert "current_identify_run_id" not in columns
    assert count(connection, "matches") == 1
    assert count(connection, "videos") == 1

    migrate(connection, "upgrade", "head")
    assert count(connection, "matches") == 1
