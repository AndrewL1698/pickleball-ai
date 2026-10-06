"""Migration 0004: metadata columns, their constraints, and one active job.

SQLite with foreign keys on, as in `test_migration_0003.py`; the PostgreSQL
run of the same data is in `test_migrations.py`.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from tests_support_api import (
    ALEMBIC_INI,
    MATCHES,
    PHASE_1,
    assert_at_0004,
    assert_upgraded,
    seed_phase_1,
)


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


def seeded_at_head(connection: Connection) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seed_phase_1(connection)
    connection.commit()
    migrate(connection, "upgrade", "head")


def test_existing_rows_gain_empty_metadata_and_lose_calibration_required(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seeded = seed_phase_1(connection)
    connection.commit()
    migrate(connection, "upgrade", MATCHES)
    assert_upgraded(connection, seeded)

    migrate(connection, "upgrade", "head")
    assert_at_0004(connection, seeded)
    assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_the_downgrade_drops_the_columns_and_keeps_every_row(connection: Connection) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seeded = seed_phase_1(connection)
    connection.commit()
    migrate(connection, "upgrade", "head")
    migrate(connection, "downgrade", MATCHES)

    columns = {row.name for row in connection.execute(text("PRAGMA table_info(videos)"))}
    assert "width" not in columns and "metadata_extracted_at" not in columns
    assert connection.execute(text("SELECT count(*) FROM videos")).scalar_one() == len(
        seeded.videos
    )
    assert connection.execute(text("SELECT count(*) FROM analysis_jobs")).scalar_one() == len(
        seeded.jobs
    )
    # Statuses 0004 reset stay reset: `uploaded` is valid at 0003 too.
    statuses = set(connection.execute(text("SELECT status FROM matches")).scalars())
    assert "calibration_required" not in statuses

    migrate(connection, "upgrade", "head")
    assert_at_0004(connection, seeded)


def _a_video_id(connection: Connection) -> str:
    return str(connection.execute(text("SELECT id FROM videos LIMIT 1")).scalar_one())


COMPLETE = (
    "width = 1920, height = 1080, rotation_degrees = 0, average_fps = 29.97, "
    "duration_seconds = 10.0, frame_count = 300, metadata_extracted_at = CURRENT_TIMESTAMP"
)


def test_complete_metadata_is_accepted(connection: Connection) -> None:
    seeded_at_head(connection)
    connection.execute(text(f"UPDATE videos SET {COMPLETE} WHERE id = :id"),
                       {"id": _a_video_id(connection)})
    connection.commit()


@pytest.mark.parametrize(
    "assignment",
    [
        "width = 0",
        "height = -5",
        "average_fps = 0",
        "duration_seconds = -1",
        "frame_count = -1",
        "rotation_degrees = 45",
        "rotation_degrees = -90",
    ],
)
def test_implausible_metadata_is_refused_by_the_database(
    connection: Connection, assignment: str
) -> None:
    seeded_at_head(connection)
    with pytest.raises(IntegrityError):
        connection.execute(
            text(f"UPDATE videos SET {COMPLETE}, {assignment} WHERE id = :id"),
            {"id": _a_video_id(connection)},
        )


@pytest.mark.parametrize(
    "assignment",
    [
        # Half a record: dimensions without the rest.
        "width = 1920, height = 1080",
        # Everything except the timestamp that says it was decoded.
        COMPLETE.replace(", metadata_extracted_at = CURRENT_TIMESTAMP", ""),
        # A codec on a video that was never decoded.
        "codec = 'avc1'",
    ],
)
def test_metadata_is_all_or_nothing(connection: Connection, assignment: str) -> None:
    seeded_at_head(connection)
    with pytest.raises(IntegrityError):
        connection.execute(
            text(f"UPDATE videos SET {assignment} WHERE id = :id"),
            {"id": _a_video_id(connection)},
        )


def test_a_null_codec_is_allowed_on_decoded_metadata(connection: Connection) -> None:
    """Some streams do not name their codec; that is not a half-written record."""
    seeded_at_head(connection)
    connection.execute(text(f"UPDATE videos SET {COMPLETE}, codec = NULL WHERE id = :id"),
                       {"id": _a_video_id(connection)})
    connection.commit()


def _insert_job(connection: Connection, match_id: str, status: str) -> None:
    connection.execute(
        text(
            "INSERT INTO analysis_jobs (id, match_id, status, stage, progress, created_at) "
            "VALUES (:id, :match_id, :status, 'ingested', 0, CURRENT_TIMESTAMP)"
        ),
        {"id": uuid.uuid4().hex, "match_id": match_id, "status": status},
    )


def test_a_match_can_have_only_one_active_job(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    match_id = uuid.uuid4().hex
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at) "
            "VALUES (:id, 'm', 'uploaded', CURRENT_TIMESTAMP)"
        ),
        {"id": match_id},
    )
    # Any amount of history is fine...
    for status in ("failed", "failed", "ready"):
        _insert_job(connection, match_id, status)
    _insert_job(connection, match_id, "queued")
    connection.commit()
    # ...but not a second queued or running job.
    for status in ("queued", "running"):
        with pytest.raises(IntegrityError):
            _insert_job(connection, match_id, status)
        connection.rollback()
