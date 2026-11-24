"""Players jobs: Detect over a calibrated match's steady part, checkpointed by chunk.

Saving a match's first calibration queues one of these. It runs Detect (ADR 0001)
and ends at `players_ready`; Identify will join it later. It never changes the
match's status: progress, failure and the stage reached live on the job.

A new Detect run is made with the newest calibration, over the steady part of the
video that holds that calibration's reference frame (`steady_part`). The run is
committed before any work starts, and so is each chunk of its output as it
finishes, so a job that dies keeps everything up to its last finished chunk. A
later players job finds the unfinished run and continues it from its first
unfinished chunk, with the run's own calibration and configuration, never the
newest, so the result is identical to a run that was never interrupted.

Unlike a processor, this module writes to the database as it goes: each chunk
has to be saved before the next one starts, which is the whole point.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID

import numpy as np

from pickleball_api.config import Settings
from pickleball_api.db import session_scope
from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.jobs import transition
from pickleball_api.models import (
    AnalysisJob,
    CourtCalibration,
    JobKind,
    JobStage,
    JobStatus,
    Match,
    ModelRun,
    ModelRunStage,
)
from pickleball_api.run_artifacts import write_detect_chunk
from pickleball_api.storage import ObjectNotFound, Storage, discard_on_error
from pickleball_ml.players.detect import (
    DetectBackend,
    DetectChunk,
    DetectConfig,
    DetectRunConfig,
    TrackingUnavailable,
    detect_chunks,
    load_yolo_backend,
)
from pickleball_ml.video.reader import VideoReadError
from pickleball_ml.video.steady_part import CameraMove, UndefinedSteadyPart, steady_part
from pickleball_worker.job_updates import JobProgressWriter, finish_job

logger = logging.getLogger(__name__)

#: Progress stops short of 1.0 until the job is finished.
MAX_RUNNING_PROGRESS = 0.99


@dataclass(frozen=True)
class DetectSetup:
    """How a players job runs Detect: the configuration a new run gets, and how to
    load the detector for a configuration. A continued run uses its own."""

    config: DetectConfig
    load_backend: Callable[[DetectConfig], DetectBackend]


def default_detect_setup(settings: Settings) -> DetectSetup:
    """YOLO from `settings.player_model_path`, with the default configuration."""
    return DetectSetup(
        config=DetectConfig(),
        load_backend=partial(load_yolo_backend, settings.player_model_path),
    )


@dataclass(frozen=True)
class _ContinuedRun:
    """An unfinished Detect run, as recorded: continued exactly as it was started."""

    run_id: UUID
    config: DetectRunConfig
    model_version: str
    chunks_done: int


@dataclass(frozen=True)
class _Plan:
    """What the claim learned: which run to make or continue, and from where."""

    match_id: UUID
    video_key: str
    duration_seconds: float
    #: A complete Detect run exists, so there is nothing to do.
    already_detected: bool
    #: The unfinished run to continue, or None to start one.
    continued: _ContinuedRun | None
    #: The run's calibration when continuing; the newest one when starting.
    calibration_id: UUID
    homography: list[list[float]]
    reference_seconds: float | None
    camera_moves: tuple[CameraMove, ...] | None


def run_players_job(job_id: UUID, storage: Storage, detect: DetectSetup) -> dict[str, Any] | None:
    """Run one players job to a terminal status."""
    plan = _claim(job_id)
    if plan is None:
        return None
    try:
        summary = _run_detect(plan, storage, detect, JobProgressWriter(job_id))
    except JobFailure as exc:
        logger.warning("job %s failed: %s", job_id, exc.detail or exc.code)
        finish_job(job_id, JobStatus.FAILED, error_code=exc.code)
        return None
    except Exception:
        logger.exception("job %s failed unexpectedly", job_id)
        finish_job(job_id, JobStatus.FAILED, error_code=JobErrorCode.INTERNAL)
        return None
    finish_job(job_id, JobStatus.READY, stage=JobStage.PLAYERS_READY)
    logger.info("job %s ready: %s", job_id, summary)
    return summary


def _claim(job_id: UUID) -> _Plan | None:
    """Move the job to `running` and read everything the run needs, in one transaction."""
    with session_scope() as session:
        job = session.get(AnalysisJob, job_id, with_for_update=True)
        if job is None or job.kind is not JobKind.PLAYERS:
            logger.warning("job %s is not a players job", job_id)
            return None
        if job.status is not JobStatus.QUEUED:
            logger.info("job %s is already %s; not running it again", job_id, job.status)
            return None
        match = job.match
        video = match.video
        metadata = None if video is None else video.decoded_metadata
        if video is None or metadata is None:
            logger.warning("job %s: match %s has no decoded video", job_id, match.id)
            transition(job, JobStatus.FAILED, error_code=JobErrorCode.MISSING_VIDEO_FILE)
            return None
        newest = match.latest_calibration
        if newest is None:
            logger.warning("job %s: match %s has no calibration", job_id, match.id)
            transition(job, JobStatus.FAILED, error_code=JobErrorCode.INTERNAL)
            return None

        transition(job, JobStatus.RUNNING, stage=JobStage.COURT_READY, progress=0.0)
        run = match.unfinished_detect_run
        calibration = newest if run is None else run.calibration
        return _Plan(
            match_id=match.id,
            video_key=video.storage_key,
            duration_seconds=metadata.duration_seconds,
            already_detected=match.complete_detect_run is not None,
            continued=None if run is None else _ContinuedRun(
                run_id=run.id,
                config=DetectRunConfig.from_config(run.config_json),
                model_version=run.model_version,
                chunks_done=len(run.chunks),
            ),
            calibration_id=calibration.id,
            homography=calibration.homography,
            reference_seconds=calibration.reference_frame.position_seconds,
            camera_moves=None if video.camera_jumps is None else tuple(
                CameraMove(jump["from_seconds"], jump["to_seconds"])
                for jump in video.camera_jumps
            ),
        )


def _run_detect(
    plan: _Plan, storage: Storage, detect: DetectSetup, report: JobProgressWriter
) -> dict[str, Any]:
    if plan.already_detected:
        return {"processor": "players", "detect": "already complete"}

    continued = plan.continued
    if continued is None:
        try:
            steady = steady_part(plan.camera_moves, plan.reference_seconds)
        except UndefinedSteadyPart as exc:
            raise JobFailure(JobErrorCode.CAMERA_MOVED, str(exc)) from exc
        config = detect.config
    else:
        steady, config = continued.config.steady, continued.config.detect

    try:
        backend = detect.load_backend(config)
    except TrackingUnavailable as exc:
        raise JobFailure(JobErrorCode.TRACKING_UNAVAILABLE, str(exc)) from exc

    if continued is None:
        run_id = _start_run(plan, backend.model_version, DetectRunConfig(
            detect=config, steady=steady, runtime=backend.runtime
        ))
        first_chunk = 0
    elif backend.model_version != continued.model_version:
        # Continuing with another model would not reproduce the finished chunks.
        raise JobFailure(
            JobErrorCode.MODEL_CHANGED,
            f"run {continued.run_id} used {continued.model_version}, this worker has"
            f" {backend.model_version}",
        )
    else:
        run_id, first_chunk = continued.run_id, continued.chunks_done
        if dict(backend.runtime) != dict(continued.config.runtime):
            # Same model and settings, so the output should match; a new device or
            # library version can still shift a box by a pixel. Say so, and go on.
            logger.warning(
                "run %s continues on %s, not %s", run_id, dict(backend.runtime),
                dict(continued.config.runtime),
            )

    end = steady.end_seconds if steady.end_seconds is not None else plan.duration_seconds
    span = max(end - steady.start_seconds, 1e-9)

    def progress(seconds: float) -> None:
        done = (seconds - steady.start_seconds) / span
        report(JobStage.COURT_READY, min(MAX_RUNNING_PROGRESS, max(0.0, done)))

    saved = 0
    try:
        with storage.local_path(plan.video_key) as path:
            for chunk in detect_chunks(
                path,
                homography=np.asarray(plan.homography, dtype=np.float64),
                config=config,
                steady=steady,
                detector=backend.detector,
                new_tracker=backend.new_tracker,
                first_chunk=first_chunk,
                progress=progress,
            ):
                _save_chunk(storage, run_id, chunk)
                saved += 1
    except (ObjectNotFound, FileNotFoundError) as exc:
        raise JobFailure(JobErrorCode.MISSING_VIDEO_FILE, "video not in storage") from exc
    except VideoReadError as exc:
        raise JobFailure(JobErrorCode.UNREADABLE_VIDEO, str(exc)) from exc
    return {
        "processor": "players",
        "detect_run": str(run_id),
        "resumed_at_chunk": first_chunk,
        "chunks_saved": saved,
        "steadiness": steady.steadiness,
    }


def _start_run(plan: _Plan, model_version: str, config: DetectRunConfig) -> UUID:
    """Commit a new, empty Detect run before any work starts."""
    with session_scope() as session:
        match = session.get(Match, plan.match_id)
        calibration = session.get(CourtCalibration, plan.calibration_id)
        assert match is not None and calibration is not None
        run = ModelRun.start(
            match,
            ModelRunStage.DETECT,
            calibration=calibration,
            model_version=model_version,
            config=config.to_config(),
        )
        session.flush()
        return run.id


def _save_chunk(storage: Storage, run_id: UUID, chunk: DetectChunk) -> None:
    """Commit one chunk's object and row together, before the next chunk starts."""
    with session_scope() as session:
        run = session.get(ModelRun, run_id, with_for_update=True)
        assert run is not None
        row = write_detect_chunk(
            storage,
            run,
            index=chunk.index,
            start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds,
            frame=chunk.detections,
            is_last=chunk.is_last,
        )
        with discard_on_error(storage, row.storage_key):
            session.commit()
