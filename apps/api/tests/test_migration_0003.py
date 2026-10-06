"""Migration 0003 against data: Phase 1 rows must survive, both ways.

These stop at 0003 itself (`MATCHES`), because 0004 deliberately changes the
statuses 0003 derived; `test_migration_0004.py` covers that step.

Runs on SQLite with foreign keys enforced, which is where a careless
`batch_alter_table` would quietly cascade-delete every job (see the migration's
docstring). The same checks run against PostgreSQL in `test_migrations.py`.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.pool import StaticPool
from tests_support_api import (
    ALEMBIC_INI,
    MATCHES,
    PHASE_1,
    assert_downgraded,
    assert_upgraded,
    seed_phase_1,
)


@pytest.fixture
def connection() -> Iterator[Connection]:
    """An empty in-memory SQLite database with foreign keys on."""
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


def test_phase_1_rows_become_matches(connection: Connection) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seeded = seed_phase_1(connection)
    connection.commit()

    migrate(connection, "upgrade", MATCHES)
    assert_upgraded(connection, seeded)


def test_the_upgrade_leaves_the_foreign_keys_intact(connection: Connection) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seed_phase_1(connection)
    connection.commit()
    migrate(connection, "upgrade", MATCHES)
    assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_the_downgrade_restores_phase_1_without_losing_a_row(connection: Connection) -> None:
    migrate(connection, "upgrade", PHASE_1)
    seeded = seed_phase_1(connection)
    connection.commit()

    migrate(connection, "upgrade", MATCHES)
    migrate(connection, "downgrade", PHASE_1)
    assert_downgraded(connection, seeded)
    assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    tables = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table'")
    ).scalars().all()
    assert "matches" not in tables

    # And back up again, from the restored rows.
    migrate(connection, "upgrade", MATCHES)
    assert_upgraded(connection, seeded)


def test_the_downgrade_refuses_a_job_it_cannot_represent(connection: Connection) -> None:
    """A job whose match has no video has no Phase 1 home. Refusing is better
    than deleting it."""
    migrate(connection, "upgrade", MATCHES)
    connection.execute(
        text(
            "INSERT INTO matches (id, name, status, created_at) "
            "VALUES (lower(hex(randomblob(16))), 'no video', 'uploaded', CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO analysis_jobs (id, match_id, status, stage, progress, created_at) "
            "SELECT lower(hex(randomblob(16))), id, 'queued', 'ingested', 0, CURRENT_TIMESTAMP "
            "FROM matches"
        )
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="no video"):
        migrate(connection, "downgrade", PHASE_1)


def test_an_empty_database_migrates_both_ways(connection: Connection) -> None:
    migrate(connection, "upgrade", "head")
    migrate(connection, "downgrade", "base")
    migrate(connection, "upgrade", "head")
