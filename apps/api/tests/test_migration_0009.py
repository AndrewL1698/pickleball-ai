"""Migration 0009: what kind of work each analysis job does.

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
from tests_support_api import ALEMBIC_INI

BEFORE = "0008_model_runs"
THIS = "0009_analysis_job_kind"
MATCH = "00000000000000000000000000000001"


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


def insert_match(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at)"
            " VALUES (:id, 'match', 'court_ready', CURRENT_TIMESTAMP)"
        ),
        {"id": MATCH},
    )


def insert_job(
    connection: Connection, identifier: str, status: str = "ready", kind: str | None = None
) -> None:
    columns = "id, match_id, status, stage, progress, created_at"
    values = ":id, :match, :status, 'metadata_ready', 1.0, CURRENT_TIMESTAMP"
    if kind is not None:
        columns, values = f"{columns}, kind", f"{values}, :kind"
    connection.execute(
        text(f"INSERT INTO analysis_jobs ({columns}) VALUES ({values})"),
        {"id": identifier, "match": MATCH, "status": status, "kind": kind},
    )


def kinds(connection: Connection) -> list[str]:
    return list(connection.execute(text("SELECT kind FROM analysis_jobs ORDER BY id")).scalars())


def test_every_existing_job_becomes_a_preparation_job(connection: Connection) -> None:
    migrate(connection, "upgrade", BEFORE)
    insert_match(connection)
    insert_job(connection, "000000000000000000000000000000a1")
    insert_job(connection, "000000000000000000000000000000a2", status="failed")
    connection.commit()

    migrate(connection, "upgrade", THIS)
    assert kinds(connection) == ["preparation", "preparation"]


def test_a_job_inserted_without_a_kind_is_a_preparation_job(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    insert_job(connection, "000000000000000000000000000000a1")
    insert_job(connection, "000000000000000000000000000000a2", kind="players")
    assert kinds(connection) == ["preparation", "players"]


def test_an_unknown_kind_is_refused(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    with pytest.raises(IntegrityError):
        insert_job(connection, "000000000000000000000000000000a1", kind="identify")


def test_a_match_still_has_one_active_job_at_most_whatever_its_kind(
    connection: Connection,
) -> None:
    migrate(connection, "upgrade", BEFORE)
    insert_match(connection)
    insert_job(connection, "000000000000000000000000000000a1", status="ready")
    connection.commit()
    migrate(connection, "upgrade", THIS)

    insert_job(connection, "000000000000000000000000000000a2", status="running")
    with pytest.raises(IntegrityError):
        insert_job(connection, "000000000000000000000000000000a3", status="queued", kind="players")


def test_a_downgrade_forgets_the_kind_and_keeps_the_jobs(connection: Connection) -> None:
    migrate(connection, "upgrade", THIS)
    insert_match(connection)
    insert_job(connection, "000000000000000000000000000000a1", kind="players")
    connection.commit()

    migrate(connection, "downgrade", BEFORE)
    assert "kind" not in {c["name"] for c in inspect(connection).get_columns("analysis_jobs")}
    assert connection.execute(text("SELECT count(*) FROM analysis_jobs")).scalar_one() == 1
    assert connection.execute(text("SELECT count(*) FROM matches")).scalar_one() == 1
