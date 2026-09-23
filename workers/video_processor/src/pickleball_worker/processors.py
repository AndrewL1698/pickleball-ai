"""What actually gets done to a video, behind one interface.

Checkpoint 1 ships `PlaceholderProcessor`, which does not analyze anything. It
exists so that the surrounding machinery -- claim the job, report progress,
reach a terminal status, survive a crash -- can be built and tested without
waiting minutes for YOLO to run, and so the real processor has a shape to fit
into. Phase 3 replaces it with one that calls `pickleball_ml`; nothing outside
this module should need to change, which is the point of the interface.

A processor is handed a `VideoRef`, not the ORM object. It reads bytes through
`Storage` and reports progress through a callback, so it cannot reach the
database, and a future version running on a separate GPU host does not have to.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import JobStage
from pickleball_api.storage import ObjectNotFound, Storage

READ_CHUNK_BYTES = 1024 * 1024

#: Called with the stage reached and how far through the job is, 0.0 to 1.0.
ProgressReporter = Callable[[JobStage, float], None]


@dataclass(frozen=True)
class VideoRef:
    """Everything a processor is told about the video it is working on."""

    id: UUID
    storage_key: str
    original_filename: str
    content_type: str
    byte_size: int


@runtime_checkable
class VideoProcessor(Protocol):
    """One pass over an uploaded video.

    Returning normally means the job succeeded. Raising `JobFailure` fails it
    with a specific reason; any other exception fails it as an internal error
    and is logged with its traceback.
    """

    name: str
    version: str

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> dict[str, Any]:
        """Do the work, returning a small summary for the job log."""


class PlaceholderProcessor:
    """Reads the stored file and fingerprints it. No computer vision.

    The digest is deterministic, cheap, needs no model weights, and is not
    entirely throwaway: it is how a re-upload of the same match will later be
    recognized. It is emphatically not analysis, and the job stops at
    `METADATA_READY` to say so.
    """

    name = "placeholder"
    version = "1"

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> dict[str, Any]:
        report(JobStage.INGESTED, 0.0)
        digest = hashlib.sha256()
        read = 0
        try:
            with storage.open(video.storage_key) as stream:
                while chunk := stream.read(READ_CHUNK_BYTES):
                    digest.update(chunk)
                    read += len(chunk)
                    if video.byte_size:
                        report(JobStage.INGESTED, min(read / video.byte_size, 0.99))
        except ObjectNotFound as exc:
            raise JobFailure(
                JobErrorCode.MISSING_VIDEO_FILE, f"missing object {video.storage_key}"
            ) from exc

        if read != video.byte_size:
            raise JobFailure(
                JobErrorCode.UNREADABLE_VIDEO,
                f"stored {video.byte_size} bytes, read {read}",
            )
        report(JobStage.METADATA_READY, 1.0)
        return {
            "processor": self.name,
            "processor_version": self.version,
            "sha256": digest.hexdigest(),
            "bytes_read": read,
        }


def default_processor() -> VideoProcessor:
    """The processor this checkpoint runs. One place to change in Phase 3."""
    return PlaceholderProcessor()
