"""Concurrent metadata-job requests against PostgreSQL.

The SQLite tests can only simulate a race. This one runs one: many requests
for the same match at once, through the real endpoint, on the database whose
row locks and partial unique index are what actually prevent a double job.

    docker compose up -d postgres
    uv run pytest -m integration
"""

import threading
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from tests_support_api import ALEMBIC_INI

from pickleball_api.config import Settings
from pickleball_api.db import get_session
from pickleball_api.dependencies import get_queue, get_storage
from pickleball_api.main import create_app
from pickleball_api.models import AnalysisJob, JobStatus, Match, MatchStatus, Video
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage

pytestmark = pytest.mark.integration

REQUESTS = 8


@pytest.fixture
def postgres_sessions(fresh_database: str) -> Iterator[sessionmaker[Session]]:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", fresh_database.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(fresh_database, pool_size=REQUESTS + 2)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_concurrent_requests_create_exactly_one_job(
    postgres_sessions: sessionmaker[Session],
    settings: Settings,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
) -> None:
    with postgres_sessions() as session:
        match = Match(name="race", status=MatchStatus.FAILED)
        match.video = Video(
            original_filename="race.mp4", storage_key=uuid.uuid4().hex + ".mp4",
            content_type="video/mp4", byte_size=10,
        )
        session.add_all([match, AnalysisJob(match=match, status=JobStatus.FAILED)])
        session.commit()
        match_id = match.id

    app = create_app(settings)

    def session_override() -> Iterator[Session]:
        with postgres_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue

    statuses: list[int] = []
    start = threading.Barrier(REQUESTS)
    with TestClient(app, raise_server_exceptions=False) as client:

        def request() -> None:
            start.wait()
            response = client.post(f"/api/matches/{match_id}/metadata-jobs")
            statuses.append(response.status_code)

        threads = [threading.Thread(target=request) for _ in range(REQUESTS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert sorted(statuses) == [202] + [409] * (REQUESTS - 1)
    with postgres_sessions() as session:
        jobs = session.scalars(
            select(AnalysisJob).where(AnalysisJob.match_id == match_id)
        ).all()
    assert sorted(job.status for job in jobs) == [JobStatus.FAILED, JobStatus.QUEUED]
    assert len(queue.enqueued) == 1
