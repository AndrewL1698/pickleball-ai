"""Running a job to a terminal status, and refusing to run one twice."""

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from pickleball_api import db
from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Match, MatchStatus, Video
from pickleball_api.storage import LocalFileStorage, Storage
from pickleball_worker.processors import (
    PlaceholderProcessor,
    ProgressReporter,
    VideoRef,
    default_processor,
)
from pickleball_worker.tasks import run_analysis_job


class FailingProcessor:
    """A processor that fails the way a real one would."""

    name = "failing"
    version = "1"

    def __init__(self, code: JobErrorCode = JobErrorCode.UNREADABLE_VIDEO) -> None:
        self.code = code

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> dict[str, Any]:
        report(JobStage.INGESTED, 0.5)
        raise JobFailure(self.code, f"the decoder gave up on {video.storage_key}")


class ExplodingProcessor:
    """A processor with a bug in it."""

    name = "exploding"
    version = "1"

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> dict[str, Any]:
        raise ZeroDivisionError("/Users/andrew/secret/path division by zero")


def reload(job_id: uuid.UUID) -> AnalysisJob:
    with db.session_scope() as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None
        return job


def match_status(job_id: uuid.UUID) -> MatchStatus:
    with db.session_scope() as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None
        return job.match.status


def test_a_successful_job_ends_ready_with_a_fingerprint(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    video, job = stored_video
    summary = run_analysis_job(str(job.id), processor=default_processor(), storage=storage)

    assert summary is not None
    with storage.open(video.storage_key) as stream:
        assert summary["sha256"] == hashlib.sha256(stream.read()).hexdigest()
    assert summary["bytes_read"] == video.byte_size

    finished = reload(job.id)
    assert finished.status is JobStatus.READY
    assert finished.stage is JobStage.METADATA_READY
    assert finished.progress == 1.0
    assert finished.started_at is not None and finished.finished_at is not None
    assert finished.error_code is None and finished.error_message is None


def test_the_same_video_always_fingerprints_the_same(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    video, job = stored_video
    ref = VideoRef(
        video.id, video.match_id, video.storage_key, "m.mp4", "video/mp4", video.byte_size
    )
    processor = PlaceholderProcessor()
    noop: ProgressReporter = lambda stage, progress: None  # noqa: E731
    first = processor.process(ref, storage, noop)
    assert first == processor.process(ref, storage, noop)


def test_a_failing_job_records_a_code_and_never_the_exception_text(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    assert run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage) is None

    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.UNREADABLE_VIDEO.value
    assert failed.error_message == "The video could not be read."
    assert "decoder gave up" not in (failed.error_message or "")
    assert failed.finished_at is not None


def test_an_unexpected_exception_fails_the_job_without_leaking_a_path(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    run_analysis_job(str(job.id), processor=ExplodingProcessor(), storage=storage)

    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.INTERNAL.value
    assert "/Users/" not in (failed.error_message or "")
    assert "ZeroDivision" not in (failed.error_message or "")


def test_a_job_whose_file_is_gone_fails_cleanly(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    video, job = stored_video
    storage.delete(video.storage_key)
    run_analysis_job(str(job.id), storage=storage)

    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.MISSING_VIDEO_FILE.value


def test_a_truncated_file_fails_rather_than_reporting_success(
    engine: Engine, storage: LocalFileStorage
) -> None:
    from pickleball_api.storage import new_storage_key

    key = new_storage_key(".mp4")
    storage.write(key, [b"only a few bytes"], max_bytes=100)
    match = Match(name="m")
    Video(
        match=match, original_filename="m.mp4", storage_key=key, content_type="video/mp4",
        byte_size=999_999,  # the row disagrees with the file
    )
    job = AnalysisJob(match=match)
    with db.session_scope() as session:
        session.add(match)

    run_analysis_job(str(job.id), storage=storage)
    assert reload(job.id).error_code == JobErrorCode.UNREADABLE_VIDEO.value


def test_rerunning_a_finished_job_leaves_its_verdict_alone(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """RQ retries a task whose worker died. That must not restart a job that
    already finished, nor overwrite the first attempt's answer."""
    _, job = stored_video
    run_analysis_job(str(job.id), storage=storage)
    first = reload(job.id)
    assert first.status is JobStatus.READY

    assert run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage) is None
    second = reload(job.id)
    assert second.status is JobStatus.READY
    assert second.error_code is None
    assert second.finished_at == first.finished_at


def test_a_job_that_is_already_running_is_not_claimed_again(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    with db.session_scope() as session:
        claimed = session.get(AnalysisJob, job.id)
        assert claimed is not None
        claimed.status = JobStatus.RUNNING

    assert run_analysis_job(str(job.id), storage=storage) is None
    assert reload(job.id).status is JobStatus.RUNNING


def test_a_job_id_that_does_not_exist_is_a_no_op(
    engine: Engine, storage: LocalFileStorage
) -> None:
    assert run_analysis_job(str(uuid.uuid4()), storage=storage) is None


def test_progress_is_recorded_while_the_job_runs(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    seen: list[tuple[JobStatus, float]] = []

    class WatchingProcessor:
        name, version = "watching", "1"

        def process(
            self, video: VideoRef, s: Storage, report: ProgressReporter
        ) -> dict[str, Any]:
            for fraction in (0.25, 0.5, 0.75):
                report(JobStage.INGESTED, fraction)
                current = reload(job.id)
                seen.append((current.status, current.progress))
            return {}

    run_analysis_job(str(job.id), processor=WatchingProcessor(), storage=storage)
    assert seen == [
        (JobStatus.RUNNING, 0.25),
        (JobStatus.RUNNING, 0.5),
        (JobStatus.RUNNING, 0.75),
    ]
    assert reload(job.id).status is JobStatus.READY


def test_tiny_progress_changes_are_not_written(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """A multi-gigabyte video must not produce an UPDATE per megabyte."""
    _, job = stored_video

    class ChattyProcessor:
        name, version = "chatty", "1"

        def process(
            self, video: VideoRef, s: Storage, report: ProgressReporter
        ) -> dict[str, Any]:
            for step in range(1000):
                report(JobStage.INGESTED, step / 1000)
            return {}

    run_analysis_job(str(job.id), processor=ChattyProcessor(), storage=storage)
    assert reload(job.id).status is JobStatus.READY


@pytest.mark.parametrize("processor", [PlaceholderProcessor(), FailingProcessor()])
def test_processors_satisfy_the_interface(processor: object) -> None:
    from pickleball_worker.processors import VideoProcessor

    assert isinstance(processor, VideoProcessor)


def test_a_successful_job_leaves_its_match_waiting_for_calibration(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """Not "ready": nothing about the match has been analysed, and the court
    still has to be calibrated."""
    _, job = stored_video
    assert match_status(job.id) is MatchStatus.UPLOADED
    run_analysis_job(str(job.id), storage=storage)
    assert match_status(job.id) is MatchStatus.CALIBRATION_REQUIRED


def test_the_match_is_processing_while_its_job_runs(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    seen: list[MatchStatus] = []

    class WatchingProcessor:
        name, version = "watching", "1"

        def process(
            self, video: VideoRef, s: Storage, report: ProgressReporter
        ) -> dict[str, Any]:
            seen.append(match_status(job.id))
            assert video.match_id == job.match_id
            return {}

    run_analysis_job(str(job.id), processor=WatchingProcessor(), storage=storage)
    assert seen == [MatchStatus.PROCESSING]


def test_a_failed_job_fails_its_match(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage)
    assert match_status(job.id) is MatchStatus.FAILED


def test_a_job_whose_match_has_no_video_fails_without_running(
    engine: Engine, storage: LocalFileStorage
) -> None:
    match = Match(name="no video")
    job = AnalysisJob(match=match)
    with db.session_scope() as session:
        session.add(match)

    ran: list[bool] = []

    class RecordingProcessor:
        name, version = "recording", "1"

        def process(
            self, video: VideoRef, s: Storage, report: ProgressReporter
        ) -> dict[str, Any]:
            ran.append(True)
            return {}

    assert run_analysis_job(str(job.id), processor=RecordingProcessor(), storage=storage) is None
    assert ran == []
    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.MISSING_VIDEO_FILE.value
    assert match_status(job.id) is MatchStatus.FAILED


def test_rerunning_a_finished_job_does_not_move_its_match(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    run_analysis_job(str(job.id), storage=storage)
    run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage)
    assert match_status(job.id) is MatchStatus.CALIBRATION_REQUIRED
