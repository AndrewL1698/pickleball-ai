"""Fixtures for the worker tests.

The worker reaches the database through `pickleball_api.db.session_scope`,
which uses a process-wide engine, so these tests point that engine at a
migrated SQLite database rather than passing a session around.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pickleball_api import db
from pickleball_api.models import AnalysisJob, Video
from pickleball_api.storage import LocalFileStorage, new_storage_key

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"

FTYP_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A migrated SQLite database, installed as the process-wide engine."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        config = Config(str(ALEMBIC_INI))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_session_factory", factory)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with db.get_session_factory()() as session:
        yield session


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "uploads")


@pytest.fixture
def stored_video(
    engine: Engine, storage: LocalFileStorage
) -> tuple[Video, AnalysisJob]:
    """A queued job whose video really is in storage."""
    content = FTYP_HEADER + b"\x00" * 1000
    key = new_storage_key(".mp4")
    size = storage.write(key, [content], max_bytes=len(content) + 1)
    video = Video(
        original_filename="match.mp4",
        storage_key=key,
        content_type="video/mp4",
        byte_size=size,
    )
    job = AnalysisJob(video=video)
    with db.session_scope() as session:
        session.add_all([video, job])
    return video, job
