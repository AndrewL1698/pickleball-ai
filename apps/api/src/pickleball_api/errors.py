"""Failure reasons that are safe to show a user.

A raw exception string is not safe to persist: a SQLAlchemy `OperationalError`
contains the connection string, password included, and most other exceptions
carry absolute filesystem paths. So a failed job records a code from this
module and the fixed sentence that goes with it, while the traceback is logged
on the server only.
"""

import enum


class JobErrorCode(enum.StrEnum):
    """Why an analysis job failed."""

    MISSING_VIDEO_FILE = "missing_video_file"
    UNREADABLE_VIDEO = "unreadable_video"
    ENQUEUE_FAILED = "enqueue_failed"
    ABANDONED = "abandoned"
    INTERNAL = "internal"


JOB_ERROR_MESSAGES: dict[JobErrorCode, str] = {
    JobErrorCode.MISSING_VIDEO_FILE: "The uploaded file is no longer in storage.",
    JobErrorCode.UNREADABLE_VIDEO: "The video could not be read.",
    JobErrorCode.ENQUEUE_FAILED: "The job could not be queued for processing.",
    JobErrorCode.ABANDONED: "Processing stopped unexpectedly and did not finish.",
    JobErrorCode.INTERNAL: "Processing failed because of an internal error.",
}


class JobFailure(Exception):
    """Raised by a processor to fail a job with a specific, user-safe reason."""

    def __init__(self, code: JobErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail  # logged, never persisted or returned
        super().__init__(f"{code}: {detail}" if detail else str(code))

    @property
    def message(self) -> str:
        return JOB_ERROR_MESSAGES[self.code]
