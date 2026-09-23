"""The task RQ runs, and the transactions around it.

Three separate units of work, for three different reasons:

- Claiming the job is its own committed transaction, so the `running` status is
  visible to the API immediately rather than at the end.
- Each progress report commits, for the same reason. A status nobody can see
  until the job finishes is not a status.
- The terminal transition commits last, and tolerates being refused: if this is
  a re-run of a job that already reached `ready`, the state machine says no and
  that is the correct answer, not an error.

The task takes a job id and nothing else. Everything it needs it reads from
PostgreSQL, so the queue is not a channel through which paths, filenames, or
configuration can be injected.
"""

import logging
from typing import Any
from uuid import UUID

from pickleball_api.config import Settings, get_settings
from pickleball_api.db import session_scope
from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.jobs import InvalidJobTransition, report_progress, transition
from pickleball_api.models import AnalysisJob, JobStage, JobStatus
from pickleball_api.storage import LocalFileStorage, Storage
from pickleball_worker.processors import VideoProcessor, VideoRef, default_processor

logger = logging.getLogger(__name__)


def run_analysis_job(
    job_id: str,
    processor: VideoProcessor | None = None,
    storage: Storage | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Run one analysis job to a terminal status.

    The optional arguments exist for tests and for a future processor that is
    chosen per job; RQ calls this with the id alone.
    """
    settings = settings or get_settings()
    processor = processor or default_processor()
    storage = storage or LocalFileStorage(settings.upload_dir)
    identifier = UUID(job_id)

    video = _claim(identifier)
    if video is None:
        return None

    try:
        summary = processor.process(video, storage, _ProgressReporter(identifier))
    except JobFailure as exc:
        logger.warning("job %s failed: %s", identifier, exc.detail or exc.code)
        _finish(identifier, JobStatus.FAILED, error_code=exc.code)
        return None
    except Exception:
        # The traceback is the operator's; the user gets the code alone.
        logger.exception("job %s failed unexpectedly", identifier)
        _finish(identifier, JobStatus.FAILED, error_code=JobErrorCode.INTERNAL)
        return None

    _finish(identifier, JobStatus.READY, stage=JobStage.METADATA_READY)
    logger.info("job %s ready: %s", identifier, summary)
    return summary


def _claim(job_id: UUID) -> VideoRef | None:
    """Move the job to `running` and return what the processor needs.

    Returns None when there is nothing to do: no such job, or a job some other
    attempt has already taken. The row is locked while it is inspected, so two
    workers handed the same id cannot both claim it.
    """
    with session_scope() as session:
        job = session.get(AnalysisJob, job_id, with_for_update=True)
        if job is None:
            logger.warning("job %s does not exist", job_id)
            return None
        if job.status is not JobStatus.QUEUED:
            logger.info("job %s is already %s; not running it again", job_id, job.status)
            return None
        transition(job, JobStatus.RUNNING, stage=JobStage.INGESTED, progress=0.0)
        video = job.video
        return VideoRef(
            id=video.id,
            storage_key=video.storage_key,
            original_filename=video.original_filename,
            content_type=video.content_type,
            byte_size=video.byte_size,
        )


class _ProgressReporter:
    """Writes a running job's progress, but not on every chunk read.

    A multi-gigabyte video would otherwise produce thousands of UPDATEs that no
    one will ever look at, so a report is skipped unless the stage changed or
    progress moved by at least a percent.
    """

    def __init__(self, job_id: UUID, min_step: float = 0.01) -> None:
        self._job_id = job_id
        self._min_step = min_step
        self._stage: JobStage | None = None
        self._progress = -1.0

    def __call__(self, stage: JobStage, progress: float) -> None:
        if stage is self._stage and progress - self._progress < self._min_step:
            return
        self._stage, self._progress = stage, progress
        with session_scope() as session:
            job = session.get(AnalysisJob, self._job_id)
            if job is None or job.status is not JobStatus.RUNNING:
                return
            report_progress(job, stage, progress)


def _finish(
    job_id: UUID,
    status: JobStatus,
    *,
    stage: JobStage | None = None,
    error_code: JobErrorCode | None = None,
) -> None:
    """Record the outcome, unless the job is no longer ours to finish."""
    with session_scope() as session:
        job = session.get(AnalysisJob, job_id, with_for_update=True)
        if job is None:
            logger.warning("job %s vanished before it could be finished", job_id)
            return
        try:
            transition(job, status, stage=stage, error_code=error_code)
        except InvalidJobTransition:
            # Already terminal. A retry must not overwrite the first verdict.
            logger.warning(
                "job %s is %s; refusing to record %s", job_id, job.status, status
            )
            session.rollback()
