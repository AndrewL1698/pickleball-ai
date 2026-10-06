"""Running a job to a terminal status, and refusing to run one twice."""

import uuid

import pytest
from sqlalchemy import Engine

from pickleball_api import db
from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Match, MatchStatus, Video
from pickleball_api.storage import LocalFileStorage, Storage, new_storage_key
from pickleball_api.testing import video_bytes
from pickleball_worker.processors import (
    ExtractedMetadata,
    MetadataProcessor,
    ProcessingResult,
    ProgressReporter,
    VideoRef,
    default_processor,
)
from pickleball_worker.tasks import run_analysis_job

METADATA = ExtractedMetadata(
    width=1920, height=1080, rotation_degrees=0, average_fps=29.97,
    duration_seconds=10.0, frame_count=300, codec="avc1",
)


def result(metadata: ExtractedMetadata = METADATA) -> ProcessingResult:
    return ProcessingResult(metadata=metadata, summary={"processor": "test"})


class FailingProcessor:
    """A processor that fails the way a real one would."""

    name = "failing"
    version = "1"

    def __init__(self, code: JobErrorCode = JobErrorCode.UNREADABLE_VIDEO) -> None:
        self.code = code

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
        report(JobStage.INGESTED, 0.5)
        raise JobFailure(self.code, f"the decoder gave up on {video.storage_key}")


class ExplodingProcessor:
    """A processor with a bug in it."""

    name = "exploding"
    version = "1"

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
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


def reload_video(job_id: uuid.UUID) -> Video:
    with db.session_scope() as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None and job.match.video is not None
        return job.match.video


class Fixed:
    """A processor that always returns the same metadata."""

    name, version = "fixed", "1"

    def __init__(self, metadata: ExtractedMetadata = METADATA) -> None:
        self.metadata = metadata

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
        return result(self.metadata)


def test_a_real_video_is_decoded_and_its_metadata_saved(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """End to end with the real reader: a 64x48 clip tagged 90 degrees."""
    _, job = stored_video
    summary = run_analysis_job(str(job.id), processor=default_processor(), storage=storage)
    assert summary == {"processor": "metadata", "processor_version": "1"}

    finished = reload(job.id)
    assert finished.status is JobStatus.READY
    assert finished.stage is JobStage.METADATA_READY
    assert finished.progress == 1.0
    assert finished.started_at is not None and finished.finished_at is not None
    assert finished.error_code is None and finished.error_message is None

    metadata = reload_video(job.id).decoded_metadata
    assert metadata is not None
    # Display orientation: stored 64x48, rotated 90 degrees, plays as 48x64.
    assert (metadata.width, metadata.height, metadata.rotation_degrees) == (48, 64, 90)
    assert metadata.average_fps == pytest.approx(10.0)
    assert metadata.frame_count == 20
    assert metadata.duration_seconds == pytest.approx(2.0)
    assert metadata.codec  # whatever this OpenCV build calls mp4v
    assert match_status(job.id) is MatchStatus.CALIBRATION_REQUIRED


def test_the_default_processor_is_the_metadata_processor() -> None:
    assert isinstance(default_processor(), MetadataProcessor)


def test_processed_metadata_is_saved_exactly(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    run_analysis_job(str(job.id), processor=Fixed(), storage=storage)
    video = reload_video(job.id)
    assert (video.width, video.height, video.rotation_degrees) == (1920, 1080, 0)
    assert video.average_fps == pytest.approx(29.97)
    assert (video.duration_seconds, video.frame_count, video.codec) == (10.0, 300, "avc1")
    assert video.metadata_extracted_at is not None


def test_a_failed_decode_leaves_metadata_absent_not_zero(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    _, job = stored_video
    run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage)
    video = reload_video(job.id)
    assert video.decoded_metadata is None
    assert video.width is None and video.frame_count is None and video.codec is None
    assert reload(job.id).error_code == JobErrorCode.UNREADABLE_VIDEO.value
    assert match_status(job.id) is MatchStatus.FAILED


def test_fake_video_bytes_fail_to_decode_with_the_safe_code(
    engine: Engine, storage: LocalFileStorage
) -> None:
    """The upload sniff accepts an `ftyp` header; the decoder is the real check."""
    content = video_bytes(4096)
    key = new_storage_key(".mp4")
    storage.write(key, [content], max_bytes=len(content))
    match = Match(name="fake")
    Video(
        match=match, original_filename="fake.mp4", storage_key=key,
        content_type="video/mp4", byte_size=len(content),
    )
    job = AnalysisJob(match=match)
    with db.session_scope() as session:
        session.add(match)

    assert run_analysis_job(str(job.id), storage=storage) is None
    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.UNREADABLE_VIDEO.value
    assert failed.error_message == "The video could not be read."
    assert reload_video(job.id).decoded_metadata is None
    assert match_status(job.id) is MatchStatus.FAILED


def test_metadata_that_cannot_be_saved_fails_the_job_instead_of_finishing_it(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """The terminal state is set only once persistence succeeds. Here the
    database refuses the values, so the job must fail -- not be ready without
    metadata, and not be left running."""
    _, job = stored_video
    impossible = ExtractedMetadata(
        width=-1, height=1080, rotation_degrees=0, average_fps=30.0,
        duration_seconds=1.0, frame_count=30, codec=None,
    )
    assert run_analysis_job(str(job.id), processor=Fixed(impossible), storage=storage) is None
    failed = reload(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == JobErrorCode.INTERNAL.value
    assert failed.stage is not JobStage.METADATA_READY
    assert reload_video(job.id).decoded_metadata is None
    assert match_status(job.id) is MatchStatus.FAILED


def test_a_job_finished_elsewhere_keeps_its_verdict_and_gets_no_metadata(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    """Terminal idempotence: if the job reached a terminal state while this
    attempt was decoding, this attempt must not overwrite it."""
    _, job = stored_video

    class Overtaken:
        name, version = "overtaken", "1"

        def process(
            self, video: VideoRef, s: Storage, report: ProgressReporter
        ) -> ProcessingResult:
            with db.session_scope() as session:
                other = session.get(AnalysisJob, job.id)
                assert other is not None
                other.status = JobStatus.FAILED
            return result()

    assert run_analysis_job(str(job.id), processor=Overtaken(), storage=storage) is None
    assert reload(job.id).status is JobStatus.FAILED
    assert reload_video(job.id).decoded_metadata is None


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
    extracted_at = reload_video(job.id).metadata_extracted_at

    assert run_analysis_job(str(job.id), processor=FailingProcessor(), storage=storage) is None
    second = reload(job.id)
    assert second.status is JobStatus.READY
    assert second.error_code is None
    assert second.finished_at == first.finished_at
    assert reload_video(job.id).metadata_extracted_at == extracted_at


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
        ) -> ProcessingResult:
            for fraction in (0.25, 0.5, 0.75):
                report(JobStage.INGESTED, fraction)
                current = reload(job.id)
                seen.append((current.status, current.progress))
            return result()

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
        ) -> ProcessingResult:
            for step in range(1000):
                report(JobStage.INGESTED, step / 1000)
            return result()

    run_analysis_job(str(job.id), processor=ChattyProcessor(), storage=storage)
    assert reload(job.id).status is JobStatus.READY


@pytest.mark.parametrize("processor", [MetadataProcessor(), FailingProcessor()])
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
        ) -> ProcessingResult:
            seen.append(match_status(job.id))
            assert video.match_id == job.match_id
            return result()

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
        ) -> ProcessingResult:
            ran.append(True)
            return result()

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
