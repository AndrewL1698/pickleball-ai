"""Fixtures for the API tests.

The suite needs neither PostgreSQL nor Redis. The database is SQLite, built by
running the real Alembic migration rather than `metadata.create_all`, so a
migration that does not produce the schema the models expect fails here rather
than on somebody's first `docker compose up`. `test_migrations.py` checks the
same thing against PostgreSQL, but only when it is running.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests_support_api import ALEMBIC_INI

from pickleball_api.config import Settings
from pickleball_api.db import get_session
from pickleball_api.dependencies import get_queue, get_storage
from pickleball_api.main import create_app
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage


def migrate(connection: Connection) -> None:
    """Run every migration on an open connection.

    Handing Alembic the connection is what lets an in-memory SQLite database be
    migrated at all: it exists only as long as its connection does.
    """
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest.fixture
def engine() -> Iterator[Engine]:
    """One in-memory SQLite database per test, with the schema migrated in.

    `StaticPool` plus `check_same_thread=False` keeps every connection pointed
    at the same database: `sqlite://` otherwise hands out a fresh empty one per
    connection, and FastAPI runs synchronous routes on a worker thread.
    """
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: object, _record: object) -> None:
        # Off by default in SQLite, which would make the tests more permissive
        # than production -- the one direction that is never acceptable.
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        migrate(connection)
        connection.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=1024 * 1024,
    )


@pytest.fixture
def storage(settings: Settings) -> LocalFileStorage:
    return LocalFileStorage(settings.upload_dir)


@pytest.fixture
def queue() -> RecordingJobQueue:
    return RecordingJobQueue()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    settings: Settings,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
) -> Iterator[TestClient]:
    """The app, wired to the test database, a temporary directory, and a fake queue."""
    app = create_app(settings)

    def session_override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue
    # raise_server_exceptions=False: an unhandled error should reach the app's
    # own handler, which is what the tests are checking, rather than being
    # re-raised into the test as if no handler existed.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
