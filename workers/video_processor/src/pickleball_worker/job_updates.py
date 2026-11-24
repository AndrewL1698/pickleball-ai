"""Writing a running job's progress and its outcome, each in its own transaction.

Shared by every kind of job. Each write commits at once, so a status the API can
see is written as soon as it is true, and each refuses to touch a job that is no
longer this attempt's.
"""

import logging
from uuid import UUID

from pickleball_api.db import session_scope
from pickleball_api.errors import JobErrorCode
from pickleball_api.jobs import InvalidJobTransition, report_progress, transition
from pickleball_api.models import AnalysisJob, JobStage, JobStatus

logger = logging.getLogger(__name__)


class JobProgressWriter:
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


def finish_job(
    job_id: UUID,
    status: JobStatus,
    *,
    stage: JobStage | None = None,
    error_code: JobErrorCode | None = None,
) -> None:
    """Record the outcome, unless the job is no longer ours to finish.

    The match's status moves with the job, in the same commit, when the job's
    kind moves it at all.
    """
    with session_scope() as session:
        job = session.get(AnalysisJob, job_id, with_for_update=True)
        if job is None:
            logger.warning("job %s vanished before it could be finished", job_id)
            return
        try:
            transition(job, status, stage=stage, error_code=error_code)
        except InvalidJobTransition:
            # Already terminal. A retry must not overwrite the first verdict.
            # `transition` refuses before it mutates anything, so there is
            # nothing to undo and the surrounding commit is a no-op.
            logger.warning(
                "job %s is %s; refusing to record %s", job_id, job.status, status
            )
