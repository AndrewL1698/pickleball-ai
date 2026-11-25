"""Players jobs: Detect over a calibrated match's steady part, checkpointed by chunk,
and Identify after every chunk.

Saving a match's first calibration queues one of these. It runs Detect and Identify
(ADR 0001) and ends at `players_ready`. It never changes the match's status:
progress, failure and the stage reached live on the job.

A new Detect run is made with the newest calibration, over the steady part of the
video that holds that calibration's reference frame (`steady_part`). The run is
committed before any work starts, and so is each chunk of its output as it
finishes, so a job that dies keeps everything up to its last finished chunk. A
later players job finds the unfinished run and continues it from its first
unfinished chunk, with the run's own calibration and configuration, never the
newest, so the result is identical to a run that was never interrupted.

After each chunk is saved, Identify runs on every chunk finished so far, projected
with the match's newest calibration. Its run, its artifact, and the match pointing
at it are committed together, so player tracks fill in while a long match is still
being detected, and a worker killed outright still leaves the last Identify run. The
first Identify run also creates the match's four players. A job whose Detect is
already complete runs Identify once. If Identify raises, the job fails with
`identify_failed`; the Detect chunks are kept, so a new players job continues Detect,
or runs Identify alone.

Unlike a processor, this module writes to the database as it goes: each chunk
has to be saved before the next one starts, which is the whole point.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd

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
    utcnow,
)
from pickleball_api.run_artifacts import read_detect_chunks, write_detect_chunk, write_run_artifact
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
from pickleball_ml.players.identify import (
    Identified,
    IdentifyRunConfig,
    identify,
    model_version,
)
from pickleball_ml.players.identity import IdentityConfig
from pickleball_ml.video.reader import VideoReadError
from pickleball_ml.video.steady_part import CameraMove, UndefinedSteadyPart, steady_part
from pickleball_worker.job_updates import JobProgressWriter, finish_job

logger = logging.getLogger(__name__)

#: Progress stops short of 1.0 until the job is finished.
MAX_RUNNING_PROGRESS = 0.99

#: Changeovers are an input to Identify, and nothing records them yet.
NO_CHANGEOVERS: tuple[float, ...] = ()


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
class IdentifySetup:
    """How a players job runs Identify: its configuration, and the function itself,
    which tests replace to make it fail."""

    config: IdentityConfig = field(default_factory=IdentityConfig)
    run: Callable[..., Identified] = identify


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
    #: The complete Detect run, if there is one: then only Identify runs.
    complete_run_id: UUID | None
    #: The unfinished run to continue, or None to start one.
    continued: _ContinuedRun | None
    #: The run's calibration when continuing; the newest one when starting.
    calibration_id: UUID
    homography: list[list[float]]
    reference_seconds: float | None
    camera_moves: tuple[CameraMove, ...] | None


@dataclass
class _IdentifyLog:
    """How long each Identify run took, for the job's summary."""

    seconds: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "identify_runs": len(self.seconds),
            "identify_seconds_last": round(self.seconds[-1], 3) if self.seconds else None,
            "identify_seconds_total": round(sum(self.seconds), 3),
        }


def run_players_job(
    job_id: UUID, storage: Storage, detect: DetectSetup, identify_with: IdentifySetup | None = None
) -> dict[str, Any] | None:
    """Run one players job to a terminal status."""
    plan = _claim(job_id)
    if plan is None:
        return None
    try:
        summary = _run(plan, storage, detect, identify_with or IdentifySetup(),
                       JobProgressWriter(job_id))
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
        complete = match.complete_detect_run
        calibration = newest if run is None else run.calibration
        return _Plan(
            match_id=match.id,
            video_key=video.storage_key,
            duration_seconds=metadata.duration_seconds,
            complete_run_id=None if complete is None else complete.id,
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


def _run(
    plan: _Plan,
    storage: Storage,
    detect: DetectSetup,
    identify_with: IdentifySetup,
    report: JobProgressWriter,
) -> dict[str, Any]:
    log = _IdentifyLog()
    if plan.complete_run_id is not None:
        detected = _read_chunks(storage, plan.complete_run_id)
        _identify(storage, plan.match_id, plan.complete_run_id, detected, identify_with, log)
        return {"processor": "players", "detect": "already complete", **log.summary()}
    return _run_detect(plan, storage, detect, identify_with, report, log)


def _run_detect(
    plan: _Plan,
    storage: Storage,
    detect: DetectSetup,
    identify_with: IdentifySetup,
    report: JobProgressWriter,
    log: _IdentifyLog,
) -> dict[str, Any]:
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

    # Every chunk finished so far, kept in memory: Identify reads them all each time.
    detected = [] if first_chunk == 0 else [_read_chunks(storage, run_id)]
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
                key = _save_chunk(storage, run_id, chunk)
                saved += 1
                # As stored, so a continued run's Identify reads exactly the same rows.
                with storage.local_path(key) as chunk_path:
                    detected.append(pd.read_parquet(chunk_path))
                _identify(storage, plan.match_id, run_id, _concat(detected), identify_with, log)
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
        **log.summary(),
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


def _save_chunk(storage: Storage, run_id: UUID, chunk: DetectChunk) -> str:
    """Commit one chunk's object and row together, before the next chunk starts, and
    return the object's key."""
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
        return row.storage_key


def _read_chunks(storage: Storage, run_id: UUID) -> pd.DataFrame:
    """The finished chunks of a Detect run, for Identify."""
    with session_scope() as session:
        run = session.get(ModelRun, run_id)
        assert run is not None
        try:
            return read_detect_chunks(storage, run)
        except ObjectNotFound as exc:
            raise JobFailure(JobErrorCode.IDENTIFY_FAILED, f"run {run_id}: {exc}") from exc


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if len(frame)]
    if not non_empty:
        return frames[-1]
    return non_empty[0] if len(non_empty) == 1 else pd.concat(non_empty, ignore_index=True)


@dataclass(frozen=True)
class _IdentifyInput:
    """What an Identify run reads besides the detections, as it stood when it started."""

    calibration_id: UUID
    homography: list[list[float]]
    config: IdentifyRunConfig


def _identify(
    storage: Storage,
    match_id: UUID,
    detect_run_id: UUID,
    detections: pd.DataFrame,
    identify_with: IdentifySetup,
    log: _IdentifyLog,
) -> None:
    """Identify over `detections` (every finished chunk of the Detect run), and commit
    the run, its artifact, the match's players and the match pointing at it together."""
    started_at = utcnow()
    started = time.perf_counter()
    inputs = _identify_input(match_id, detect_run_id, identify_with.config)
    try:
        result = identify_with.run(
            detections,
            np.asarray(inputs.homography, dtype=np.float64),
            identify_with.config,
            changeovers_seconds=inputs.config.changeovers_seconds,
        )
    except Exception as exc:
        logger.exception("Identify over run %s failed", detect_run_id)
        raise JobFailure(JobErrorCode.IDENTIFY_FAILED, str(exc)) from exc
    config = IdentifyRunConfig(
        identity=inputs.config.identity,
        detect_chunks=inputs.config.detect_chunks,
        tracked_until_seconds=inputs.config.tracked_until_seconds,
        steady_part=inputs.config.steady_part,
        changeovers_seconds=result.changeovers_seconds,
    )

    with session_scope() as session:
        match = session.get(Match, match_id, with_for_update=True)
        detect = session.get(ModelRun, detect_run_id)
        calibration = session.get(CourtCalibration, inputs.calibration_id)
        assert match is not None and detect is not None and calibration is not None
        run = ModelRun.start(
            match,
            ModelRunStage.IDENTIFY,
            calibration=calibration,
            model_version=model_version(),
            config=config.to_config(),
            parent=detect,
        )
        run.started_at = started_at
        key = write_run_artifact(storage, run, result.tracks)
        run.completed_at = utcnow()
        match.ensure_players()
        match.current_identify_run = run
        with discard_on_error(storage, key):
            session.commit()

    elapsed = time.perf_counter() - started
    log.seconds.append(elapsed)
    tracks = result.tracks
    logger.info(
        "Identify over %d chunk(s) to %.1f s: %d rows, %d frames with 4 players, %.2f s",
        config.detect_chunks, config.tracked_until_seconds, len(tracks),
        int((tracks.groupby("frame_number").size() == 4).sum()), elapsed,
    )


def _identify_input(
    match_id: UUID, detect_run_id: UUID, identity: IdentityConfig
) -> _IdentifyInput:
    """The newest calibration, and what of the Detect run Identify is about to read."""
    with session_scope() as session:
        match = session.get(Match, match_id)
        detect = session.get(ModelRun, detect_run_id)
        assert match is not None and detect is not None
        calibration = match.latest_calibration
        assert calibration is not None
        chunks = detect.chunks
        return _IdentifyInput(
            calibration_id=calibration.id,
            homography=calibration.homography,
            config=IdentifyRunConfig(
                identity=identity,
                detect_chunks=len(chunks),
                tracked_until_seconds=chunks[-1].end_seconds if chunks else 0.0,
                steady_part=detect.config_json["steady_part"],
                changeovers_seconds=NO_CHANGEOVERS,
            ),
        )
