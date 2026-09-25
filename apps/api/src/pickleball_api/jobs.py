"""The lifecycle of an analysis job.

A job is a small state machine, and both the API and the worker drive it, so
the rules live here rather than in either caller. Every move goes through
`transition`, which refuses anything not on the map below.

The awkward case is a retry. RQ will re-run a task whose worker was killed, and
that second attempt must not be able to march a job that already reached `ready`
back through `running`. `ready` therefore has no outgoing moves at all, and the
worker treats a refused transition as "somebody else already finished this"
rather than as an error to report.

A job also drives its match's status, and only here: `transition` is the one
place a job's status changes, so it is the one place that can keep
`Match.status` from contradicting the job it summarizes.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from pickleball_api.errors import JOB_ERROR_MESSAGES, JobErrorCode
from pickleball_api.models import (
    AnalysisJob,
    JobStage,
    JobStatus,
    Match,
    MatchStatus,
    utcnow,
)

logger = logging.getLogger(__name__)

#: The only status changes a job may make.
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.READY, JobStatus.FAILED}),
    JobStatus.READY: frozenset(),
    # A failed job can be queued again; nothing else may follow a failure.
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
}

#: The match status that a job reaching each status implies.
MATCH_STATUS_FOR_JOB: dict[JobStatus, MatchStatus] = {
    # Requeued after a failure: waiting again, not yet processing.
    JobStatus.QUEUED: MatchStatus.UPLOADED,
    JobStatus.RUNNING: MatchStatus.PROCESSING,
    # Processing finished, and the court has not been calibrated. Not "ready":
    # nothing about the match has been analysed.
    JobStatus.READY: MatchStatus.CALIBRATION_REQUIRED,
    JobStatus.FAILED: MatchStatus.FAILED,
}


class InvalidJobTransition(Exception):
    """A job was asked to move to a status it cannot reach from its current one."""

    def __init__(self, job_id: UUID, current: JobStatus, requested: JobStatus) -> None:
        self.job_id = job_id
        self.current = current
        self.requested = requested
        super().__init__(f"job {job_id}: cannot move from {current} to {requested}")


def can_transition(current: JobStatus, requested: JobStatus) -> bool:
    return requested in ALLOWED_TRANSITIONS[current]


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
        job.started_at = utcnow()
        job.finished_at = None
        job.error_code = None
        job.error_message = None
    elif status is JobStatus.READY:
        job.finished_at = utcnow()
        job.progress = 1.0
        job.error_code = None
        job.error_message = None
    elif status is JobStatus.FAILED:
        job.finished_at = utcnow()
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
    _sync_match_status(job)
    return job


def _sync_match_status(job: AnalysisJob) -> None:
    """Make the match's status agree with the job that just moved.

    A calibrated match stays `court_ready` when a later job finishes: that job
    did not undo the calibration. A job with no match loaded (a bare object in
    a unit test) has nothing to update.
    """
    match = job.match
    if match is None:
        return
    target = MATCH_STATUS_FOR_JOB[job.status]
    if target is MatchStatus.CALIBRATION_REQUIRED and match.status is MatchStatus.COURT_READY:
        return
    match.status = target


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


def get_match(session: Session, match_id: UUID) -> Match | None:
    return session.scalar(
        select(Match)
        .options(selectinload(Match.video), selectinload(Match.jobs))
        .where(Match.id == match_id)
    )


def list_matches(session: Session, *, limit: int = 100, offset: int = 0) -> Sequence[Match]:
    """Matches, newest first, each with its video and jobs loaded.

    `selectinload` issues one extra query per relationship for the whole page,
    instead of the two per match a naive lazy load would issue behind the list
    endpoint.
    """
    statement = (
        select(Match)
        .options(selectinload(Match.video), selectinload(Match.jobs))
        .order_by(Match.created_at.desc(), Match.id)
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(statement).all()
