"""Fixtures for the worker tests.

The worker reaches the database through `pickleball_api.db.session_scope`,
which uses a process-wide engine, so these tests point that engine at a
migrated SQLite database rather than passing a session around.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from pickleball_api import db
from pickleball_api.models import AnalysisJob, Video
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_api.testing import migrated_sqlite_engine, video_bytes


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A migrated SQLite database, installed as the process-wide engine."""
    with migrated_sqlite_engine() as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        monkeypatch.setattr(db, "_engine", engine)
        monkeypatch.setattr(db, "_session_factory", factory)
        yield engine


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
    content = video_bytes(1032)
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
