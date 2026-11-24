"""Players jobs end to end: Detect over the steady part, checkpointed, resumable.

A real 3.5 s fixture video is decoded; the detector and tracker are fakes, so no
model runs. Chunks are one second long so a short clip has several.
"""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Engine

from pickleball_api import db, jobs
from pickleball_api.config import Settings
from pickleball_api.errors import JobErrorCode
from pickleball_api.models import (
    AnalysisJob,
    CourtCalibration,
    JobKind,
    JobStage,
    JobStatus,
    Match,
    MatchStatus,
    ModelRun,
    ReferenceFrame,
    ReferenceFrameKind,
    Video,
    utcnow,
)
from pickleball_api.run_artifacts import read_detect_chunks
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_ml.players.detect import (
    DETECT_COLUMNS,
    DetectBackend,
    DetectConfig,
    TrackingUnavailable,
)
from pickleball_ml.video.fixtures import write_test_video
from pickleball_ml.video.reader import Frame
from pickleball_worker.players import DetectSetup, default_detect_setup
from pickleball_worker.tasks import run_analysis_job

FPS, FRAMES = 30.0, 105  # 3.5 s
#: Image pixels to court feet, shifted so both fake people stand inside the gate.
ON_COURT = [[1.0, 0.0, 0.0], [0.0, 1.0, -20.0], [0.0, 0.0, 1.0]]
#: A recalibration that would put everyone 1000 ft off court, outside the gate.
FAR_AWAY = [[1.0, 0.0, 1000.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
MODEL = "fake.pt@sha256:0123"
RUNTIME = {"device": "fake", "ultralytics": "0", "torch": "0"}


class Killed(BaseException):
    """What a worker being killed looks like from inside: nothing gets to clean up."""


class FakeDetector:
    """Two people on court in every frame; can be told to die partway through."""

    def __init__(
        self, *, die_at_frame: int | None = None, watch: Callable[[], None] | None = None
    ) -> None:
        self.seconds_seen: list[float] = []
        self.die_at_frame = die_at_frame
        self.watch = watch

    def detect(self, image: Frame) -> tuple[np.ndarray, np.ndarray]:
        if self.die_at_frame is not None and len(self.seconds_seen) == self.die_at_frame:
            raise Killed
        self.seconds_seen.append(float(image.mean()))
        if self.watch is not None:
            self.watch()
        brightness = float(image.mean()) / 255.0
        xyxy = np.array([[2.0, 10.0, 10.0, 30.0], [6.0, 5.0, 14.0, 40.0]])
        return xyxy, np.array([0.5 + brightness / 4, 0.8])


class FakeTracker:
    def update(self, xyxy: np.ndarray, confidence: np.ndarray, image: Frame) -> np.ndarray:
        return np.arange(1, len(xyxy) + 1, dtype=np.int64)


def stage(detector: FakeDetector, *, model: str = MODEL) -> DetectSetup:
    def load(config: DetectConfig) -> DetectBackend:
        return DetectBackend(
            model_version=model, detector=detector, new_tracker=FakeTracker, runtime=RUNTIME
        )

    return DetectSetup(config=DetectConfig(chunk_seconds=1.0), load_backend=load)


@pytest.fixture
def video_bytes(tmp_path: Path) -> bytes:
    return write_test_video(tmp_path / "clip.mp4", frames=FRAMES, fps=FPS).read_bytes()


@pytest.fixture
def make_match(
    engine: Engine, storage: LocalFileStorage, video_bytes: bytes
) -> Callable[..., uuid.UUID]:
    """A calibrated match with a players job queued, as saving a calibration leaves it."""

    def make(
        *,
        reference_seconds: float | None = 1.0,
        camera_jumps: list[dict[str, float]] | None = (),  # type: ignore[assignment]
        homography: list[list[float]] = ON_COURT,
    ) -> uuid.UUID:
        key = new_storage_key(".mp4")
        storage.write(key, [video_bytes], max_bytes=len(video_bytes))
        match = Match(name="match", status=MatchStatus.COURT_READY)
        match.video = Video(
            original_filename="match.mp4",
            storage_key=key,
            content_type="video/mp4",
            byte_size=len(video_bytes),
            width=64, height=48, rotation_degrees=0, average_fps=FPS,
            duration_seconds=FRAMES / FPS, frame_count=FRAMES, codec="mp4v",
            metadata_extracted_at=utcnow(),
            camera_jumps=None if camera_jumps is None else list(camera_jumps),
        )
        frame = ReferenceFrame(
            ordinal=0,
            kind=ReferenceFrameKind.SAMPLE if reference_seconds is not None
            else ReferenceFrameKind.BACKGROUND,
            position_seconds=reference_seconds,
            source_frame_count=1 if reference_seconds is not None else 25,
            storage_key=new_storage_key(".jpg"),
            width=64, height=48, byte_size=10,
        )
        match.reference_frames.append(frame)
        calibrate(match, homography)
        match.jobs.append(AnalysisJob(kind=JobKind.PREPARATION, status=JobStatus.READY))
        with db.session_scope() as session:
            session.add(match)
            session.flush()
            session.add(jobs.new_job(match, JobKind.PLAYERS))
            return match.id

    return make


def calibrate(match: Match, homography: list[list[float]]) -> CourtCalibration:
    calibration = CourtCalibration(
        reference_frame=match.reference_frames[0],
        image_points={}, court_points={}, homography=homography,
        reprojection_error_ft={}, mean_reprojection_error_ft=0.1, max_reprojection_error_ft=0.2,
    )
    match.calibrations.append(calibration)
    return calibration


def players_job(match_id: uuid.UUID) -> AnalysisJob:
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None and match.latest_job is not None
        return match.latest_job


def run_players(match_id: uuid.UUID, storage: LocalFileStorage, detect: DetectSetup) -> Any:
    return run_analysis_job(str(players_job(match_id).id), storage=storage, detect=detect)


def detect_runs(match_id: uuid.UUID) -> list[ModelRun]:
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None
        runs = match.detect_runs
        for run in runs:
            _ = run.chunks, run.calibration  # load before the session closes
        return runs


def detections(match_id: uuid.UUID, storage: LocalFileStorage) -> pd.DataFrame:
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None
        (run,) = match.detect_runs
        return read_detect_chunks(storage, run)


def match_status(match_id: uuid.UUID) -> MatchStatus:
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None
        return match.status


def queue_another_players_job(match_id: uuid.UUID) -> None:
    """What `POST /players-jobs` does, once a reaper has failed the killed job."""
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None and match.latest_job is not None
        killed = match.latest_job
        if killed.status is JobStatus.RUNNING:
            jobs.transition(killed, JobStatus.FAILED, error_code=JobErrorCode.ABANDONED)
        assert match.can_start_players_job
        session.add(jobs.new_job(match, JobKind.PLAYERS))


# --- A whole run --------------------------------------------------------------


def test_a_players_job_detects_the_whole_steady_video_chunk_by_chunk(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()
    summary = run_players(match_id, storage, stage(FakeDetector()))
    assert summary is not None

    job = players_job(match_id)
    assert (job.status, job.stage, job.progress) == (JobStatus.READY, JobStage.PLAYERS_READY, 1.0)
    assert match_status(match_id) is MatchStatus.COURT_READY

    (run,) = detect_runs(match_id)
    assert run.completed_at is not None
    assert run.artifact_storage_key is None
    assert run.model_version == MODEL
    assert [(c.index, c.start_seconds) for c in run.chunks] == [
        (0, 0.0), (1, 1.0), (2, 2.0), (3, 3.0)
    ]
    assert run.calibration.homography == ON_COURT
    config = run.config_json
    assert (config["rate_hz"], config["chunk_seconds"], config["imgsz"]) == (15.0, 1.0, 1280)
    assert config["tracker"]["track_buffer"] == 30
    assert config["runtime"] == RUNTIME
    assert config["steady_part"] == {
        "start_seconds": 0.0, "end_seconds": None, "excluded": [], "steadiness": "steady"
    }

    frame = detections(match_id, storage)
    assert list(frame.columns) == DETECT_COLUMNS
    assert frame["frame_number"].nunique() == 53  # 15 a second at 30 fps
    assert frame["in_gate"].all()
    assert frame["track_id"].is_unique is False  # tracked across frames...
    assert frame.groupby("frame_number")["track_id"].nunique().eq(2).all()  # ...two people


def test_progress_is_the_share_of_the_steady_part_done(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()
    seen: list[tuple[JobStatus, JobStage, float]] = []

    def watch() -> None:
        job = players_job(match_id)
        seen.append((job.status, job.stage, job.progress))

    run_players(match_id, storage, stage(FakeDetector(watch=watch)))
    assert {(status, stage) for status, stage, _ in seen} == {
        (JobStatus.RUNNING, JobStage.COURT_READY)
    }
    progress = [p for _, _, p in seen]
    assert progress == sorted(progress)
    assert 0.8 < progress[-1] < 1.0


# --- Killed and resumed -------------------------------------------------------


def test_a_killed_job_resumes_at_the_first_unfinished_chunk_with_identical_output(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    uninterrupted = make_match()
    run_players(uninterrupted, storage, stage(FakeDetector()))

    killed = make_match()
    # Kept frame 40 of 53 is frame 80, at 2.67 s, inside chunk 2.
    with pytest.raises(Killed):
        run_players(killed, storage, stage(FakeDetector(die_at_frame=40)))
    (run,) = detect_runs(killed)
    assert [c.index for c in run.chunks] == [0, 1]
    assert run.completed_at is None
    assert players_job(killed).status is JobStatus.RUNNING

    queue_another_players_job(killed)
    resumed = FakeDetector()
    run_players(killed, storage, stage(resumed))

    assert players_job(killed).status is JobStatus.READY
    # Chunks 0 and 1 were not detected again: 15 frames a second from 2.0 s to 3.5 s.
    assert len(resumed.seconds_seen) == 23
    (run,) = detect_runs(killed)
    assert [c.index for c in run.chunks] == [0, 1, 2, 3]
    pd.testing.assert_frame_equal(detections(killed, storage), detections(uninterrupted, storage))


def test_a_resumed_run_keeps_its_own_calibration_after_a_recalibration(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    uninterrupted = make_match()
    run_players(uninterrupted, storage, stage(FakeDetector()))

    killed = make_match()
    with pytest.raises(Killed):
        run_players(killed, storage, stage(FakeDetector(die_at_frame=40)))
    with db.session_scope() as session:
        match = session.get(Match, killed)
        assert match is not None
        calibrate(match, FAR_AWAY)
    queue_another_players_job(killed)
    run_players(killed, storage, stage(FakeDetector()))

    (run,) = detect_runs(killed)
    assert run.completed_at is not None
    assert run.calibration.homography == ON_COURT
    # The newest calibration would have gated everyone out.
    pd.testing.assert_frame_equal(detections(killed, storage), detections(uninterrupted, storage))


def test_a_run_is_not_continued_with_a_different_model(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()
    with pytest.raises(Killed):
        run_players(match_id, storage, stage(FakeDetector(die_at_frame=40)))
    queue_another_players_job(match_id)
    run_players(match_id, storage, stage(FakeDetector(), model="other.pt@sha256:9999"))
    job = players_job(match_id)
    assert (job.status, job.error_code) == (JobStatus.FAILED, "model_changed")
    assert len(detect_runs(match_id)[0].chunks) == 2


def test_a_job_for_a_match_already_detected_finishes_without_detecting(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()
    run_players(match_id, storage, stage(FakeDetector()))
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None
        session.add(jobs.new_job(match, JobKind.PLAYERS))
    detector = FakeDetector()
    run_players(match_id, storage, stage(detector))
    assert players_job(match_id).status is JobStatus.READY
    assert detector.seconds_seen == []
    assert len(detect_runs(match_id)) == 1


# --- The steady part ----------------------------------------------------------


def test_only_the_steady_part_holding_the_reference_frame_is_detected(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match(
        reference_seconds=1.0,
        camera_jumps=[{"from_seconds": 2.0, "to_seconds": 2.5, "shift_px": 107.4}],
    )
    run_players(match_id, storage, stage(FakeDetector()))
    (run,) = detect_runs(match_id)
    assert run.config_json["steady_part"] == {
        "start_seconds": 0.0,
        "end_seconds": 2.0,
        "excluded": [{"start_seconds": 2.0, "end_seconds": None, "moved_at_seconds": 2.0}],
        "steadiness": "moved",
    }
    assert [(c.index, c.end_seconds) for c in run.chunks] == [(0, 1.0), (1, 2.0)]
    assert detections(match_id, storage)["timestamp_ms"].max() < 2000.0
    assert players_job(match_id).status is JobStatus.READY


def test_an_unchecked_video_is_detected_whole_and_says_so(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match(camera_jumps=None)
    run_players(match_id, storage, stage(FakeDetector()))
    (run,) = detect_runs(match_id)
    assert run.config_json["steady_part"]["steadiness"] == "unchecked"
    assert run.completed_at is not None


def test_a_background_calibration_on_a_moved_camera_fails_with_its_own_code(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match(
        reference_seconds=None,
        camera_jumps=[{"from_seconds": 2.0, "to_seconds": 2.5, "shift_px": 107.4}],
    )
    detector = FakeDetector()
    assert run_players(match_id, storage, stage(detector)) is None
    job = players_job(match_id)
    assert (job.status, job.error_code) == (JobStatus.FAILED, "camera_moved")
    assert "single still" in (job.error_message or "")
    assert detect_runs(match_id) == []
    assert detector.seconds_seen == []
    assert match_status(match_id) is MatchStatus.COURT_READY


# --- A worker that cannot track -----------------------------------------------


def test_a_worker_without_tracking_fails_the_job_with_a_clear_code(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()

    def unavailable(config: DetectConfig) -> DetectBackend:
        raise TrackingUnavailable("ultralytics and torch are not installed")

    run_players(match_id, storage, DetectSetup(config=DetectConfig(), load_backend=unavailable))
    job = players_job(match_id)
    assert (job.status, job.error_code) == (JobStatus.FAILED, "tracking_unavailable")
    assert detect_runs(match_id) == []
    assert match_status(match_id) is MatchStatus.COURT_READY


def test_the_default_setup_without_a_model_file_fails_with_that_code(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage, tmp_path: Path
) -> None:
    """The real loader, which refuses before it would import torch."""
    match_id = make_match()
    settings = Settings(environment="test", player_model_path=tmp_path / "missing.pt")
    run_players(match_id, storage, default_detect_setup(settings))
    assert players_job(match_id).error_code == "tracking_unavailable"


def test_a_video_gone_from_storage_fails_with_the_missing_file_code(
    make_match: Callable[..., uuid.UUID], storage: LocalFileStorage
) -> None:
    match_id = make_match()
    with db.session_scope() as session:
        match = session.get(Match, match_id)
        assert match is not None and match.video is not None
        storage.delete(match.video.storage_key)
    run_players(match_id, storage, stage(FakeDetector()))
    assert players_job(match_id).error_code == "missing_video_file"
