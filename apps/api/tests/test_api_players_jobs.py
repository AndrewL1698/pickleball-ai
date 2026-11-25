"""Players jobs: queued by a calibration, resumed on request, never moving the match."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_api_calibration import clicks, make_match, post_calibration

from pickleball_api import jobs
from pickleball_api.config import Settings
from pickleball_api.models import (
    AnalysisJob,
    JobKind,
    JobStage,
    JobStatus,
    Match,
    MatchStatus,
    ModelRun,
    ModelRunStage,
    utcnow,
)
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage


def players_jobs(session: Session, match: Match) -> list[AnalysisJob]:
    session.expire_all()
    found = session.get(Match, match.id)
    assert found is not None
    return [job for job in found.jobs if job.kind is JobKind.PLAYERS]


def calibrated(client: TestClient, session: Session, storage: LocalFileStorage) -> Match:
    match = make_match(session, storage)
    assert post_calibration(client, match, clicks()).status_code == 201
    return match


def detect_run(session: Session, match: Match, *, complete: bool) -> ModelRun:
    session.expire_all()
    found = session.get(Match, match.id)
    assert found is not None and found.latest_calibration is not None
    run = ModelRun.start(
        found,
        ModelRunStage.DETECT,
        calibration=found.latest_calibration,
        model_version="yolo26m.pt@sha256:abc",
        config={"rate_hz": 15.0},
    )
    if complete:
        run.completed_at = utcnow()
    session.commit()
    return run


def identify_run(session: Session, match: Match, detect: ModelRun) -> ModelRun:
    """A finished Identify run over all of `detect`, which the match shows."""
    session.expire_all()
    found = session.get(Match, match.id)
    parent = session.get(ModelRun, detect.id)
    assert found is not None and found.latest_calibration is not None and parent is not None
    run = ModelRun.start(
        found,
        ModelRunStage.IDENTIFY,
        calibration=found.latest_calibration,
        model_version="pickleball_ml.players.identity@0.1.0",
        config={"detect_chunks": len(parent.chunks)},
        parent=parent,
    )
    run.completed_at = utcnow()
    found.current_identify_run = run
    session.commit()
    return run


def finish(session: Session, job_id: uuid.UUID, *statuses: JobStatus) -> None:
    session.expire_all()
    job = session.get(AnalysisJob, job_id)
    assert job is not None
    for status in statuses:
        jobs.transition(job, status)
    session.commit()


# --- Queued by a calibration --------------------------------------------------


def test_saving_a_calibration_queues_exactly_one_players_job(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
    settings: Settings,
) -> None:
    match = make_match(session, storage)
    response = post_calibration(client, match, clicks())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "court_ready"
    assert body["latest_job"]["kind"] == "players"
    assert body["latest_job"]["status"] == "queued"
    assert body["latest_job"]["stage"] == "court_ready"
    assert body["can_start_players_job"] is False  # one is already queued

    queued = players_jobs(session, match)
    assert len(queued) == 1
    assert queue.enqueued == [queued[0].id]
    assert queue.timeouts[queued[0].id] == settings.players_job_timeout_seconds


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_saving_a_calibration_again_while_players_job_is_active_queues_nothing(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
    status: JobStatus,
) -> None:
    match = calibrated(client, session, storage)
    (job,) = players_jobs(session, match)
    if status is JobStatus.RUNNING:
        finish(session, job.id, JobStatus.RUNNING)

    response = post_calibration(client, match, clicks())
    assert response.status_code == 201
    assert len(response.json()["jobs"]) == 2  # the preparation job and that one
    assert [j.id for j in players_jobs(session, match)] == [job.id]
    assert queue.enqueued == [job.id]


@pytest.mark.parametrize("complete", [True, False])
def test_a_recalibration_never_detects_again(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
    complete: bool,
) -> None:
    """ADR 0001. An unfinished run is continued through `/players-jobs` instead."""
    match = calibrated(client, session, storage)
    (job,) = players_jobs(session, match)
    finish(session, job.id, JobStatus.RUNNING, JobStatus.READY if complete else JobStatus.FAILED)
    detect_run(session, match, complete=complete)

    assert post_calibration(client, match, clicks()).status_code == 201
    assert len(players_jobs(session, match)) == 1
    assert queue.enqueued == [job.id]


def test_a_preparation_job_still_blocks_a_calibration(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage, job_status=JobStatus.RUNNING)
    assert post_calibration(client, match, clicks()).status_code == 409


def test_a_calibration_is_saved_even_when_its_players_job_cannot_be_queued(
    client: TestClient, session: Session, storage: LocalFileStorage, queue: RecordingJobQueue
) -> None:
    queue.available = False
    match = make_match(session, storage)
    response = post_calibration(client, match, clicks())
    assert response.status_code == 201
    body = response.json()
    assert body["calibration"] is not None
    assert body["status"] == "court_ready"
    assert body["latest_job"]["status"] == "failed"
    assert body["latest_job"]["error_code"] == "enqueue_failed"
    assert body["can_start_players_job"] is True


# --- The match's status -------------------------------------------------------


@pytest.mark.parametrize(
    "statuses",
    [
        (),
        (JobStatus.RUNNING,),
        (JobStatus.RUNNING, JobStatus.READY),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.FAILED, JobStatus.QUEUED),
    ],
)
def test_a_calibrated_match_stays_court_ready_whatever_its_players_job_does(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    statuses: tuple[JobStatus, ...],
) -> None:
    match = calibrated(client, session, storage)
    (job,) = players_jobs(session, match)
    finish(session, job.id, *statuses)
    body = client.get(f"/api/matches/{match.id}").json()
    assert body["latest_job"]["status"] == (statuses[-1] if statuses else "queued")
    assert body["status"] == "court_ready"


def test_a_new_players_job_leaves_the_match_court_ready(session: Session) -> None:
    match = Match(name="m", status=MatchStatus.COURT_READY)
    job = jobs.new_job(match, JobKind.PLAYERS)
    assert (job.kind, job.status, job.stage) == (
        JobKind.PLAYERS, JobStatus.QUEUED, JobStage.COURT_READY
    )
    assert match.status is MatchStatus.COURT_READY


# --- POST /players-jobs -------------------------------------------------------


def test_an_unfinished_detect_run_can_be_continued(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
    settings: Settings,
) -> None:
    match = calibrated(client, session, storage)
    (first,) = players_jobs(session, match)
    finish(session, first.id, JobStatus.RUNNING, JobStatus.FAILED)
    detect_run(session, match, complete=False)
    assert client.get(f"/api/matches/{match.id}").json()["can_start_players_job"] is True

    response = client.post(f"/api/matches/{match.id}/players-jobs")
    assert response.status_code == 202
    body = response.json()
    assert body["latest_job"]["kind"] == "players"
    assert body["latest_job"]["status"] == "queued"
    assert body["status"] == "court_ready"
    assert body["can_start_players_job"] is False
    second = uuid.UUID(body["latest_job"]["id"])
    assert queue.enqueued == [first.id, second]
    assert queue.timeouts[second] == settings.players_job_timeout_seconds


def test_a_players_job_can_start_again_after_one_failed_before_detecting(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    """The camera check, say, failed it before a Detect run existed."""
    match = calibrated(client, session, storage)
    (first,) = players_jobs(session, match)
    finish(session, first.id, JobStatus.RUNNING, JobStatus.FAILED)
    assert client.post(f"/api/matches/{match.id}/players-jobs").status_code == 202


def test_a_players_job_is_refused_without_a_calibration(
    client: TestClient, session: Session, storage: LocalFileStorage, queue: RecordingJobQueue
) -> None:
    match = make_match(session, storage)
    assert client.get(f"/api/matches/{match.id}").json()["can_start_players_job"] is False
    response = client.post(f"/api/matches/{match.id}/players-jobs")
    assert response.status_code == 409
    assert "not been calibrated" in response.json()["detail"]
    assert queue.enqueued == []


def test_a_players_job_is_refused_while_a_job_is_active(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = calibrated(client, session, storage)
    response = client.post(f"/api/matches/{match.id}/players-jobs")
    assert response.status_code == 409
    assert "already queued or running" in response.json()["detail"]


def test_a_players_job_is_refused_once_detect_has_finished_and_identify_has_run(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = calibrated(client, session, storage)
    (job,) = players_jobs(session, match)
    finish(session, job.id, JobStatus.RUNNING, JobStatus.READY)
    identify_run(session, match, detect_run(session, match, complete=True))
    assert client.get(f"/api/matches/{match.id}").json()["can_start_players_job"] is False
    response = client.post(f"/api/matches/{match.id}/players-jobs")
    assert response.status_code == 409
    assert "already been tracked" in response.json()["detail"]


def test_a_finished_detect_run_with_no_identify_run_can_start_an_identify_only_job(
    client: TestClient, session: Session, storage: LocalFileStorage, queue: RecordingJobQueue
) -> None:
    """Identify failed, or the worker died between Detect's last chunk and Identify:
    the job skips Detect and runs Identify."""
    match = calibrated(client, session, storage)
    (first,) = players_jobs(session, match)
    finish(session, first.id, JobStatus.RUNNING, JobStatus.FAILED)
    detect_run(session, match, complete=True)
    assert client.get(f"/api/matches/{match.id}").json()["can_start_players_job"] is True

    response = client.post(f"/api/matches/{match.id}/players-jobs")
    assert response.status_code == 202
    assert response.json()["latest_job"]["kind"] == "players"
    assert len(queue.enqueued) == 2


def test_a_players_job_needs_a_match_that_exists(client: TestClient) -> None:
    assert client.post(f"/api/matches/{uuid.uuid4()}/players-jobs").status_code == 404
