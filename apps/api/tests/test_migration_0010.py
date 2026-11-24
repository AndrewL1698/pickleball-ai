"""Migration 0010: the finished chunks of a Detect run.

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
from test_migration_0008 import DETECT, insert_match, insert_run
from tests_support_api import ALEMBIC_INI

BEFORE = "0009_analysis_job_kind"
THIS = "0010_model_run_chunks"


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


def insert_chunk(
    connection: Connection,
    index: int,
    *,
    run: str = DETECT,
    start: float | None = None,
    end: float | None = None,
    key: str | None = None,
) -> None:
    start = index * 300.0 if start is None else start
    connection.execute(
        text(
            'INSERT INTO model_run_chunks (id, run_id, "index", start_seconds, end_seconds,'
            " storage_key, completed_at)"
            " VALUES (:id, :run, :index, :start, :end, :key, CURRENT_TIMESTAMP)"
        ),
        {
            "id": f"{index + 1:032x}",
            "run": run,
            "index": index,
            "start": start,
            "end": start + 300.0 if end is None else end,
            "key": key or f"{index:032x}.parquet",
        },
    )


def detect_run(connection: Connection) -> None:
    """A calibrated match with an unfinished Detect run, which has no artifact."""
    insert_match(connection)
    insert_run(connection, completed_at=None)
    connection.execute(text("UPDATE model_runs SET artifact_storage_key = NULL"))


def count(connection: Connection) -> int:
    return int(connection.execute(text("SELECT count(*) FROM model_run_chunks")).scalar_one())


def test_existing_runs_have_no_chunks_after_the_upgrade(connection: Connection) -> None:
    migrate(connection, "upgrade", BEFORE)
    detect_run(connection)
    connection.commit()
    migrate(connection, "upgrade", THIS)
    assert count(connection) == 0
    assert connection.execute(text("SELECT count(*) FROM model_runs")).scalar_one() == 1


def test_a_run_holds_its_chunks_in_order(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    detect_run(connection)
    insert_chunk(connection, 0)
    insert_chunk(connection, 1)
    indexes = connection.execute(
        text('SELECT "index" FROM model_run_chunks ORDER BY "index"')
    ).scalars()
    assert list(indexes) == [0, 1]


@pytest.mark.parametrize(
    "values",
    [
        {"index": 0},  # a chunk is saved once
        {"index": 1, "key": f"{0:032x}.parquet"},  # an object belongs to one chunk
        {"index": -1},
        {"index": 1, "start": 600.0, "end": 599.0},
        {"index": 1, "run": "0000000000000000000000000000000f"},
    ],
)
def test_an_impossible_chunk_is_refused_by_the_database(
    connection: Connection, values: dict[str, object]
) -> None:
    migrate(connection, "upgrade", THIS)
    detect_run(connection)
    insert_chunk(connection, 0)
    with pytest.raises(IntegrityError):
        insert_chunk(connection, **values)  # type: ignore[arg-type]


def test_deleting_the_match_deletes_its_runs_chunks(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    detect_run(connection)
    insert_chunk(connection, 0)
    connection.execute(text("DELETE FROM matches"))
    assert count(connection) == 0


def test_a_downgrade_forgets_the_chunks_and_keeps_the_run(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    detect_run(connection)
    insert_chunk(connection, 0)
    connection.commit()

    migrate(connection, "downgrade", BEFORE)
    assert "model_run_chunks" not in inspect(connection).get_table_names()
    assert connection.execute(text("SELECT count(*) FROM model_runs")).scalar_one() == 1
