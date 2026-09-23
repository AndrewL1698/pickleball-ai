"""The lifecycle of an analysis job.

A job is a small state machine, and both the API and the worker drive it, so
the rules live here rather than in either caller. Every move goes through
`transition`, which refuses anything not on the map below.

The awkward case is a retry. RQ will re-run a task whose worker was killed, and
that second attempt must not be able to march a job that already reached `ready`
back through `running`. `ready` therefore has no outgoing moves at all, and the
worker treats a refused transition as "somebody else already finished this"
rather than as an error to report.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from pickleball_api.errors import JOB_ERROR_MESSAGES, JobErrorCode
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Video

logger = logging.getLogger(__name__)

#: The only status changes a job may make.
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.READY, JobStatus.FAILED}),
    JobStatus.READY: frozenset(),
    # A failed job can be queued again; nothing else may follow a failure.
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
}

TERMINAL_STATUSES = frozenset({JobStatus.READY, JobStatus.FAILED})


class InvalidJobTransition(Exception):
    """A job was asked to move to a status it cannot reach from its current one."""

    def __init__(self, job_id: UUID, current: JobStatus, requested: JobStatus) -> None:
        self.job_id = job_id
        self.current = current
        self.requested = requested
        super().__init__(f"job {job_id}: cannot move from {current} to {requested}")


def can_transition(current: JobStatus, requested: JobStatus) -> bool:
    return requested in ALLOWED_TRANSITIONS[current]


def now() -> datetime:
    """Timezone-aware present. Naive timestamps become ambiguous the moment two
    machines are involved, so every stored time carries its offset."""
    return datetime.now(UTC)


def transition(
    job: AnalysisJob,
    status: JobStatus,
    *,
    stage: JobStage | None = None,
    progress: float | None = None,
    error_code: JobErrorCode | None = None,
) -> AnalysisJob:
    """Move a job to `status`, keeping its timestamps and error fields consistent.

    Raises `InvalidJobTransition` rather than writing a contradictory row.
    """
    if not can_transition(job.status, status):
        raise InvalidJobTransition(job.id, job.status, status)

    job.status = status
    if stage is not None:
        job.stage = stage
    if progress is not None:
        job.progress = _clamp(progress)

    if status is JobStatus.RUNNING:
        job.started_at = now()
        job.finished_at = None
        job.error_code = None
        job.error_message = None
    elif status is JobStatus.READY:
        job.finished_at = now()
        job.progress = 1.0
        job.error_code = None
        job.error_message = None
    elif status is JobStatus.FAILED:
        job.finished_at = now()
        code = error_code or JobErrorCode.INTERNAL
        job.error_code = code.value
        job.error_message = JOB_ERROR_MESSAGES[code]
    elif status is JobStatus.QUEUED:
        # Requeueing an earlier failure: clear the previous attempt's outcome.
        job.started_at = None
        job.finished_at = None
        job.progress = 0.0
        job.error_code = None
        job.error_message = None
    return job


def report_progress(
    job: AnalysisJob, stage: JobStage, progress: float
) -> AnalysisJob:
    """Update how far a running job has got, without changing its status."""
    if job.status is not JobStatus.RUNNING:
        raise InvalidJobTransition(job.id, job.status, JobStatus.RUNNING)
    job.stage = stage
    job.progress = _clamp(progress)
    return job


def _clamp(progress: float) -> float:
    return min(1.0, max(0.0, progress))


def get_job(session: Session, job_id: UUID) -> AnalysisJob | None:
    return session.get(AnalysisJob, job_id)


def get_video(session: Session, video_id: UUID) -> Video | None:
    return session.scalar(
        select(Video).options(selectinload(Video.jobs)).where(Video.id == video_id)
    )


def list_videos(session: Session, *, limit: int = 100, offset: int = 0) -> Sequence[Video]:
    """Videos, newest first, each with its jobs loaded.

    `selectinload` issues one extra query for all the jobs instead of one per
    video, which is what a naive lazy load would do behind the list endpoint.
    """
    statement = (
        select(Video)
        .options(selectinload(Video.jobs))
        .order_by(Video.created_at.desc(), Video.id)
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(statement).all()
