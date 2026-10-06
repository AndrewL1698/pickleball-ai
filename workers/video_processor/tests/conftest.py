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
from pickleball_api.models import AnalysisJob, Match, Video
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_api.testing import migrated_sqlite_engine
from pickleball_ml.video.fixtures import write_test_video


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
    engine: Engine, storage: LocalFileStorage, tmp_path: Path
) -> tuple[Video, AnalysisJob]:
    """A queued job whose video really is in storage, and really decodes.

    A real 64x48, 20-frame, 10 fps clip written by OpenCV, tagged as rotated
    90 degrees, so it plays as 48x64. The fake `ftyp` header the upload tests
    use would pass the upload sniff but, correctly, fail to decode.
    """
    content = write_test_video(
        tmp_path / "source.mp4", width=64, height=48, frames=20, fps=10.0, rotation=90
    ).read_bytes()
    key = new_storage_key(".mp4")
    size = storage.write(key, [content], max_bytes=len(content) + 1)
    match = Match(name="match")
    video = Video(
        match=match,
        original_filename="match.mp4",
        storage_key=key,
        content_type="video/mp4",
        byte_size=size,
    )
    job = AnalysisJob(match=match)
    with db.session_scope() as session:
        session.add(match)
    return video, job
