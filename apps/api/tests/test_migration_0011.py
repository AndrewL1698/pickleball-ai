"""Migration 0011: where a video's playback copy is stored.

SQLite with foreign keys on, as in `test_migration_0010.py`; `test_migrations.py`
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

BEFORE = "0010_model_run_chunks"
THIS = "0011_playback_copy"


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


def insert_video(connection: Connection, number: int) -> None:
    """A match and its video, with ids and storage key made from `number`."""
    match, video = f"{number:032x}", f"{number + 100:032x}"
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at)"
            " VALUES (:id, 'match', 'calibration_required', CURRENT_TIMESTAMP)"
        ),
        {"id": match},
    )
    connection.execute(
        text(
            "INSERT INTO videos (id, match_id, original_filename, storage_key, content_type,"
            " byte_size) VALUES (:id, :match, 'm.mp4', :key, 'video/mp4', 10)"
        ),
        {"id": video, "match": match, "key": f"{number:032x}.mp4"},
    )


def set_copy(connection: Connection, number: int, key: str) -> None:
    connection.execute(
        text("UPDATE videos SET playback_storage_key = :key WHERE id = :id"),
        {"key": key, "id": f"{number + 100:032x}"},
    )


def copies(connection: Connection) -> list[str | None]:
    return list(
        connection.execute(text("SELECT playback_storage_key FROM videos ORDER BY id")).scalars()
    )


def test_existing_videos_have_no_copy_after_the_upgrade(connection: Connection) -> None:
    """Nothing is backfilled: a migration must not start work."""
    migrate(connection, "upgrade", BEFORE)
    insert_video(connection, 1)
    connection.commit()
    migrate(connection, "upgrade", THIS)
    assert copies(connection) == [None]


def test_a_video_records_its_copy(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_video(connection, 1)
    insert_video(connection, 2)
    set_copy(connection, 1, "a" * 32 + ".mp4")
    assert copies(connection) == ["a" * 32 + ".mp4", None]


def test_two_videos_cannot_share_one_copy(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_video(connection, 1)
    insert_video(connection, 2)
    set_copy(connection, 1, "a" * 32 + ".mp4")
    with pytest.raises(IntegrityError):
        set_copy(connection, 2, "a" * 32 + ".mp4")


def test_a_downgrade_drops_the_column_and_keeps_the_video(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_video(connection, 1)
    set_copy(connection, 1, "a" * 32 + ".mp4")
    connection.commit()

    migrate(connection, "downgrade", BEFORE)
    columns = {column["name"] for column in inspect(connection).get_columns("videos")}
    assert "playback_storage_key" not in columns
    assert connection.execute(text("SELECT count(*) FROM videos")).scalar_one() == 1
