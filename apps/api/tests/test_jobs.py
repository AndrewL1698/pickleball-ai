"""The job state machine, and the constraints the tables carry."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pickleball_api.errors import JOB_ERROR_MESSAGES, JobErrorCode
from pickleball_api.jobs import (
    ALLOWED_TRANSITIONS,
    InvalidJobTransition,
    can_transition,
    report_progress,
    transition,
)
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Video
from pickleball_api.schemas import JobRead, VideoDetail

LEGAL = [
    (JobStatus.QUEUED, JobStatus.RUNNING),
    (JobStatus.QUEUED, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.READY),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.FAILED, JobStatus.QUEUED),
]

ILLEGAL = [
    (JobStatus.QUEUED, JobStatus.QUEUED),
    (JobStatus.QUEUED, JobStatus.READY),
    (JobStatus.RUNNING, JobStatus.QUEUED),
    (JobStatus.RUNNING, JobStatus.RUNNING),
    (JobStatus.READY, JobStatus.RUNNING),
    (JobStatus.READY, JobStatus.READY),
    (JobStatus.READY, JobStatus.FAILED),
    (JobStatus.READY, JobStatus.QUEUED),
    (JobStatus.FAILED, JobStatus.RUNNING),
    (JobStatus.FAILED, JobStatus.READY),
    (JobStatus.FAILED, JobStatus.FAILED),
]


def make_job(status: JobStatus = JobStatus.QUEUED) -> AnalysisJob:
    return AnalysisJob(
        id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=status,
        stage=JobStage.INGESTED,
        progress=0.0,
    )


@pytest.mark.parametrize(("start", "end"), LEGAL)
def test_legal_transitions_are_allowed(start: JobStatus, end: JobStatus) -> None:
    assert can_transition(start, end)
    assert transition(make_job(start), end).status is end


@pytest.mark.parametrize(("start", "end"), ILLEGAL)
def test_illegal_transitions_are_refused(start: JobStatus, end: JobStatus) -> None:
    assert not can_transition(start, end)
    job = make_job(start)
    with pytest.raises(InvalidJobTransition):
        transition(job, end)
    assert job.status is start  # and nothing was changed on the way out


def test_every_status_has_a_transition_rule() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(JobStatus)


def test_ready_is_final_so_a_retry_cannot_undo_it() -> None:
    """The reason `ready` has no outgoing moves: RQ re-running a finished job
    must not walk it backwards into `running`."""
    assert ALLOWED_TRANSITIONS[JobStatus.READY] == frozenset()


def test_starting_a_job_records_when_and_clears_the_last_failure() -> None:
    job = make_job(JobStatus.FAILED)
    job.error_code, job.error_message = "internal", "boom"
    transition(job, JobStatus.QUEUED)
    transition(job, JobStatus.RUNNING)
    assert isinstance(job.started_at, datetime)
    assert job.finished_at is None
    assert (job.error_code, job.error_message) == (None, None)


def test_finishing_a_job_completes_its_progress() -> None:
    job = make_job()
    transition(job, JobStatus.RUNNING)
    transition(job, JobStatus.READY, stage=JobStage.METADATA_READY)
    assert job.progress == 1.0
    assert job.stage is JobStage.METADATA_READY
    assert isinstance(job.finished_at, datetime)


def test_failing_a_job_stores_a_code_and_a_fixed_message() -> None:
    job = make_job()
    transition(job, JobStatus.FAILED, error_code=JobErrorCode.UNREADABLE_VIDEO)
    assert job.error_code == "unreadable_video"
    assert job.error_message == JOB_ERROR_MESSAGES[JobErrorCode.UNREADABLE_VIDEO]
    assert isinstance(job.finished_at, datetime)


def test_failing_without_a_reason_is_an_internal_error() -> None:
    job = transition(make_job(), JobStatus.FAILED)
    assert job.error_code == JobErrorCode.INTERNAL.value


def test_requeueing_clears_the_previous_attempt() -> None:
    job = make_job()
    transition(job, JobStatus.RUNNING)
    transition(job, JobStatus.FAILED, error_code=JobErrorCode.INTERNAL)
    transition(job, JobStatus.QUEUED)
    assert (job.started_at, job.finished_at, job.progress) == (None, None, 0.0)
    assert (job.error_code, job.error_message) == (None, None)


def test_progress_is_kept_within_range() -> None:
    job = make_job()
    transition(job, JobStatus.RUNNING)
    assert report_progress(job, JobStage.INGESTED, 2.5).progress == 1.0
    assert report_progress(job, JobStage.INGESTED, -1.0).progress == 0.0


def test_progress_cannot_be_reported_for_a_job_that_is_not_running() -> None:
    with pytest.raises(InvalidJobTransition):
        report_progress(make_job(JobStatus.QUEUED), JobStage.INGESTED, 0.5)


def test_a_job_cannot_reference_a_video_that_does_not_exist(session: Session) -> None:
    session.add(make_job())
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_a_video_deletes_its_jobs(session: Session) -> None:
    video = Video(
        original_filename="m.mp4", storage_key="a" * 32 + ".mp4",
        content_type="video/mp4", byte_size=10,
    )
    session.add_all([video, AnalysisJob(video=video)])
    session.commit()
    session.delete(video)
    session.commit()
    assert session.query(AnalysisJob).count() == 0


def test_progress_outside_zero_to_one_is_rejected_by_the_database(session: Session) -> None:
    video = Video(
        original_filename="m.mp4", storage_key="b" * 32 + ".mp4",
        content_type="video/mp4", byte_size=10,
    )
    session.add_all([video, AnalysisJob(video=video, progress=1.5)])
    with pytest.raises(IntegrityError):
        session.commit()


def test_serialized_responses_never_include_the_storage_key(session: Session) -> None:
    video = Video(
        original_filename="match.mp4", storage_key="c" * 32 + ".mp4",
        content_type="video/mp4", byte_size=10,
    )
    session.add_all([video, AnalysisJob(video=video)])
    session.commit()
    body = VideoDetail.of(video).model_dump()
    assert "storage_key" not in body
    assert "storage_key" not in str(body)
    assert body["latest_job"]["status"] is JobStatus.QUEUED
    assert [j["id"] for j in body["jobs"]] == [body["latest_job"]["id"]]


def test_the_latest_job_is_the_most_recent_one(session: Session) -> None:
    video = Video(
        original_filename="match.mp4", storage_key="d" * 32 + ".mp4",
        content_type="video/mp4", byte_size=10,
    )
    first = AnalysisJob(video=video, status=JobStatus.FAILED)
    session.add_all([video, first])
    session.commit()
    second = AnalysisJob(video=video, status=JobStatus.QUEUED)
    session.add(second)
    session.commit()
    session.refresh(video)
    assert video.latest_job is not None
    assert JobRead.of(video.latest_job).id == second.id
