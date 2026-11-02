"""Migrations 0005 to 0007: calibration images, court calibrations, the camera check.

SQLite with foreign keys on, as in `test_migration_0003.py`; `test_migrations.py`
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

METADATA = "0004_video_metadata"
MATCH = "00000000000000000000000000000001"
FRAME = "00000000000000000000000000000002"


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


def insert_match(connection: Connection, status: str = "calibration_required") -> None:
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at)"
            " VALUES (:id, 'match', :status, CURRENT_TIMESTAMP)"
        ),
        {"id": MATCH, "status": status},
    )


def insert_frame(
    connection: Connection,
    *,
    identifier: str = FRAME,
    ordinal: int = 0,
    kind: str = "background",
    position: float | None = None,
    width: int = 1920,
) -> None:
    connection.execute(
        text(
            "INSERT INTO reference_frames (id, match_id, ordinal, kind, position_seconds,"
            " source_frame_count, storage_key, width, height, byte_size)"
            " VALUES (:id, :match, :ordinal, :kind, :position, 25, :key, :width, 1080, 10)"
        ),
        {
            "id": identifier,
            "match": MATCH,
            "ordinal": ordinal,
            "kind": kind,
            "position": position,
            "key": f"{identifier}.jpg",
            "width": width,
        },
    )


def insert_calibration(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO court_calibrations (id, match_id, reference_frame_id, source,"
            " image_points, court_points, homography, reprojection_error_ft,"
            " mean_reprojection_error_ft, max_reprojection_error_ft)"
            " VALUES ('00000000000000000000000000000003', :match, :frame, 'manual',"
            " '{}', '{}', '[]', '{}', 0.1, 0.2)"
        ),
        {"match": MATCH, "frame": FRAME},
    )


def test_a_match_keeps_its_rows_through_the_upgrade_and_gains_nothing(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", METADATA)
    insert_match(connection)
    connection.commit()
    migrate(connection, "upgrade", "head")
    assert connection.execute(text("SELECT status FROM matches")).scalar_one() == (
        "calibration_required"
    )
    assert connection.execute(text("SELECT count(*) FROM reference_frames")).scalar_one() == 0
    assert connection.execute(text("SELECT count(*) FROM court_calibrations")).scalar_one() == 0


def test_a_video_from_before_the_camera_check_is_not_checked_rather_than_steady(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", "0006_court_calibrations")
    insert_match(connection)
    connection.execute(
        text(
            "INSERT INTO videos (id, match_id, original_filename, storage_key, content_type,"
            " byte_size) VALUES ('00000000000000000000000000000004', :match, 'm.mp4', 'k.mp4',"
            " 'video/mp4', 10)"
        ),
        {"match": MATCH},
    )
    connection.commit()

    migrate(connection, "upgrade", "head")
    assert connection.execute(text("SELECT camera_jumps FROM videos")).scalar_one() is None

    migrate(connection, "downgrade", "0006_court_calibrations")
    assert "camera_jumps" not in {c["name"] for c in inspect(connection).get_columns("videos")}
    assert connection.execute(text("SELECT count(*) FROM videos")).scalar_one() == 1


def test_a_downgrade_removes_the_tables_and_the_promise_of_a_calibration(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection, status="court_ready")
    insert_frame(connection)
    insert_calibration(connection)
    connection.commit()

    migrate(connection, "downgrade", METADATA)
    tables = set(inspect(connection).get_table_names())
    assert not tables & {"reference_frames", "court_calibrations"}
    assert connection.execute(text("SELECT status FROM matches")).scalar_one() == (
        "calibration_required"
    )

    migrate(connection, "upgrade", "head")
    assert connection.execute(text("SELECT count(*) FROM matches")).scalar_one() == 1


def test_deleting_a_match_deletes_its_frames_and_calibrations(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_frame(connection)
    insert_calibration(connection)
    connection.execute(text("DELETE FROM matches"))
    assert connection.execute(text("SELECT count(*) FROM reference_frames")).scalar_one() == 0
    assert connection.execute(text("SELECT count(*) FROM court_calibrations")).scalar_one() == 0


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "painting"},
        {"kind": "background", "position": 12.0},
        {"kind": "sample", "position": None},
        {"width": 0},
    ],
)
def test_an_impossible_reference_frame_is_refused_by_the_database(
    connection: Connection, values: dict[str, object]
) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    with pytest.raises(IntegrityError):
        insert_frame(connection, **values)  # type: ignore[arg-type]


def test_two_frames_cannot_share_a_position_in_the_order(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    insert_match(connection)
    insert_frame(connection)
    with pytest.raises(IntegrityError):
        insert_frame(connection, identifier="00000000000000000000000000000009")
