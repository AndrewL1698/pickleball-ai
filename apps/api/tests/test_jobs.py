"""The job state machine, the match status it drives, and the constraints the tables carry."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pickleball_api.errors import JOB_ERROR_MESSAGES, JobErrorCode
from pickleball_api.jobs import (
    ALLOWED_TRANSITIONS,
    MATCH_STATUS_FOR_JOB,
    InvalidJobTransition,
    can_transition,
    report_progress,
    transition,
)
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Match, MatchStatus, Video
from pickleball_api.schemas import JobRead, MatchDetail

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
        match_id=uuid.uuid4(),
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


def make_match(name: str = "match", key_char: str = "a") -> Match:
    match = Match(name=name)
    match.video = Video(
        original_filename=f"{name}.mp4", storage_key=key_char * 32 + ".mp4",
        content_type="video/mp4", byte_size=10,
    )
    return match


def test_a_job_cannot_reference_a_match_that_does_not_exist(session: Session) -> None:
    session.add(make_job())
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_video_cannot_reference_a_match_that_does_not_exist(session: Session) -> None:
    session.add(
        Video(
            match_id=uuid.uuid4(), original_filename="m.mp4", storage_key="e" * 32 + ".mp4",
            content_type="video/mp4", byte_size=10,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_match_has_at_most_one_video(session: Session) -> None:
    match = make_match()
    session.add(match)
    session.commit()
    session.add(
        Video(
            match_id=match.id, original_filename="second.mp4", storage_key="f" * 32 + ".mp4",
            content_type="video/mp4", byte_size=10,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_a_match_deletes_its_video_and_jobs(session: Session) -> None:
    match = make_match()
    session.add_all([match, AnalysisJob(match=match), AnalysisJob(match=match)])
    session.commit()
    session.delete(match)
    session.commit()
    assert session.query(Video).count() == 0
    assert session.query(AnalysisJob).count() == 0


def test_the_database_cascades_a_match_delete_without_the_orm(session: Session) -> None:
    """`ON DELETE CASCADE` itself, not the ORM's cascade: a row deleted in SQL
    must not leave a video or a job pointing at nothing."""
    match = make_match()
    session.add_all([match, AnalysisJob(match=match)])
    session.commit()
    session.execute(delete(Match).where(Match.id == match.id))
    session.commit()
    session.expunge_all()
    assert session.query(Video).count() == 0
    assert session.query(AnalysisJob).count() == 0


def test_deleting_one_match_leaves_the_others_alone(session: Session) -> None:
    kept, dropped = make_match("kept", "a"), make_match("dropped", "b")
    session.add_all([kept, dropped, AnalysisJob(match=kept), AnalysisJob(match=dropped)])
    session.commit()
    session.delete(dropped)
    session.commit()
    assert [m.name for m in session.query(Match)] == ["kept"]
    assert session.query(Video).one().match_id == kept.id
    assert session.query(AnalysisJob).one().match_id == kept.id


def test_progress_outside_zero_to_one_is_rejected_by_the_database(session: Session) -> None:
    match = make_match(key_char="b")
    session.add_all([match, AnalysisJob(match=match, progress=1.5)])
    with pytest.raises(IntegrityError):
        session.commit()


def test_an_unknown_match_status_is_rejected_by_the_database(session: Session) -> None:
    match = make_match()
    session.add(match)
    session.commit()
    # Raw SQL, past the application's own enum validation: the CHECK is the
    # backstop for a psql session or a future migration.
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE matches SET status = 'ready'"))
        session.commit()


def test_serialized_responses_never_include_the_storage_key(session: Session) -> None:
    match = make_match(key_char="c")
    session.add_all([match, AnalysisJob(match=match)])
    session.commit()
    body = MatchDetail.of(match).model_dump()
    assert "storage_key" not in str(body)
    assert body["video"]["original_filename"] == "match.mp4"
    assert body["latest_job"]["status"] is JobStatus.QUEUED
    assert [j["id"] for j in body["jobs"]] == [body["latest_job"]["id"]]


def test_the_latest_job_is_the_most_recent_one(session: Session) -> None:
    match = make_match(key_char="d")
    first = AnalysisJob(match=match, status=JobStatus.FAILED)
    session.add_all([match, first])
    session.commit()
    second = AnalysisJob(match=match, status=JobStatus.QUEUED)
    session.add(second)
    session.commit()
    session.refresh(match)
    assert match.latest_job is not None
    assert JobRead.of(match.latest_job).id == second.id


def test_the_latest_job_does_not_depend_on_insertion_order(session: Session) -> None:
    """Ordered by `created_at`, not by whichever row the database returns last."""
    match = make_match()
    newer = AnalysisJob(match=match, created_at=datetime(2026, 9, 2, tzinfo=UTC))
    older = AnalysisJob(match=match, created_at=datetime(2026, 9, 1, tzinfo=UTC))
    session.add_all([match, newer, older])
    session.commit()
    session.expire_all()
    loaded = session.get(Match, match.id)
    assert loaded is not None
    assert [j.id for j in loaded.jobs] == [older.id, newer.id]
    assert loaded.latest_job is not None and loaded.latest_job.id == newer.id


def test_a_dead_heat_on_created_at_is_broken_by_id(session: Session) -> None:
    match = make_match()
    at = datetime(2026, 9, 1, tzinfo=UTC)
    low = AnalysisJob(id=uuid.UUID(int=1), match=match, created_at=at)
    high = AnalysisJob(id=uuid.UUID(int=2), match=match, created_at=at)
    session.add_all([match, high, low])
    session.commit()
    session.expire_all()
    loaded = session.get(Match, match.id)
    assert loaded is not None and loaded.latest_job is not None
    assert loaded.latest_job.id == high.id


def test_every_job_status_implies_a_match_status() -> None:
    assert set(MATCH_STATUS_FOR_JOB) == set(JobStatus)
    # There is no generic "ready": a finished file check is not an analysed match.
    assert MATCH_STATUS_FOR_JOB[JobStatus.READY] is MatchStatus.CALIBRATION_REQUIRED


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([JobStatus.RUNNING], MatchStatus.PROCESSING),
        ([JobStatus.RUNNING, JobStatus.READY], MatchStatus.CALIBRATION_REQUIRED),
        ([JobStatus.RUNNING, JobStatus.FAILED], MatchStatus.FAILED),
        ([JobStatus.FAILED], MatchStatus.FAILED),
        ([JobStatus.FAILED, JobStatus.QUEUED], MatchStatus.UPLOADED),
    ],
)
def test_a_job_transition_moves_its_match(
    steps: list[JobStatus], expected: MatchStatus
) -> None:
    match = Match(name="m", status=MatchStatus.UPLOADED)
    job = AnalysisJob(match=match, status=JobStatus.QUEUED, stage=JobStage.INGESTED)
    for step in steps:
        transition(job, step)
    assert match.status is expected


def test_a_refused_transition_leaves_the_match_alone() -> None:
    match = Match(name="m", status=MatchStatus.CALIBRATION_REQUIRED)
    job = AnalysisJob(match=match, status=JobStatus.READY, stage=JobStage.METADATA_READY)
    with pytest.raises(InvalidJobTransition):
        transition(job, JobStatus.FAILED)
    assert match.status is MatchStatus.CALIBRATION_REQUIRED


def test_a_finished_job_does_not_undo_a_calibration() -> None:
    match = Match(name="m", status=MatchStatus.COURT_READY)
    job = AnalysisJob(match=match, status=JobStatus.RUNNING, stage=JobStage.INGESTED)
    transition(job, JobStatus.READY)
    assert match.status is MatchStatus.COURT_READY
