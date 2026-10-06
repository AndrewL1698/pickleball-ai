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
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from pickleball_api.config import Settings, get_settings
from pickleball_api.db import get_session
from pickleball_api.dependencies import get_queue, get_storage
from pickleball_api.main import create_app
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage
from pickleball_api.testing import migrated_sqlite_engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    """One migrated in-memory SQLite database per test."""
    with migrated_sqlite_engine() as engine:
        yield engine


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
        # The host Starlette's TestClient sends. It is supplied here rather
        # than shipped in the production default.
        trusted_hosts=("localhost", "127.0.0.1", "testserver"),
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


TEST_DATABASE = "pickleball_migration_test"


@pytest.fixture
def fresh_database() -> Iterator[str]:
    """An empty PostgreSQL database, dropped again afterwards.

    For the `integration` tests; skips when no PostgreSQL is running.
    """
    admin_url = get_settings().database_url.get_secret_value()
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    target = admin_url.rsplit("/", 1)[0] + "/" + TEST_DATABASE
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    except Exception as exc:  # no PostgreSQL running
        pytest.skip(f"PostgreSQL is not available: {type(exc).__name__}")
    yield target
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}"'))
    admin.dispose()


