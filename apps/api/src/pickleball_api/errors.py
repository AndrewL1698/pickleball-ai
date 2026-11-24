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
    #: Reserved for the reaper that does not exist yet: a worker killed
    #: mid-job leaves its row in `running` for ever. Nothing writes this code
    #: today, and `docs/BACKEND.md` lists the gap.
    ABANDONED = "abandoned"
    #: The camera moved, and the calibration's image belongs to no single camera
    #: position: the background, or a still taken while the camera was moving.
    CAMERA_MOVED = "camera_moved"
    #: The worker cannot run player tracking: no ultralytics/torch, or no model file.
    TRACKING_UNAVAILABLE = "tracking_unavailable"
    INTERNAL = "internal"


JOB_ERROR_MESSAGES: dict[JobErrorCode, str] = {
    JobErrorCode.MISSING_VIDEO_FILE: "The uploaded file is no longer in storage.",
    JobErrorCode.UNREADABLE_VIDEO: "The video could not be read.",
    JobErrorCode.ENQUEUE_FAILED: "The job could not be queued for processing.",
    JobErrorCode.ABANDONED: "Processing stopped unexpectedly and did not finish.",
    JobErrorCode.CAMERA_MOVED: (
        "The camera moved during this video. Calibrate the court on a single still from"
        " the part to track, not on the background image."
    ),
    JobErrorCode.TRACKING_UNAVAILABLE: "Player tracking is not set up on this worker.",
    JobErrorCode.INTERNAL: "Processing failed because of an internal error.",
}


class JobFailure(Exception):
    """Raised by a processor to fail a job with a specific, user-safe reason."""

    def __init__(self, code: JobErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail  # logged, never persisted or returned
        super().__init__(f"{code}: {detail}" if detail else str(code))
