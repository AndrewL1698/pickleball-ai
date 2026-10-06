"""What actually gets done to a video, behind one interface.

The default processor reads the video's metadata -- dimensions, rotation,
average frame rate, duration, frame count, codec -- with
`pickleball_ml.video.reader.read_metadata`. That is all it does: no frame is
analysed, and no model is loaded. Only the lightweight video module is
imported, never anything that pulls in torch or YOLO weights.

A processor is handed a `VideoRef`, not the ORM object, and returns a typed
`ProcessingResult` rather than writing anything itself. It reads bytes through
`Storage` and reports progress through a callback, so it cannot reach the
database: the task layer persists the result, in the same transaction as the
job's terminal status. A future version running on a separate GPU host does
not have to change shape.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import JobStage
from pickleball_api.storage import ObjectNotFound, Storage
from pickleball_ml.video.reader import VideoReadError, read_metadata

#: Called with the stage reached and how far through the job is, 0.0 to 1.0.
ProgressReporter = Callable[[JobStage, float], None]


@dataclass(frozen=True)
class VideoRef:
    """Everything a processor is told about the video it is working on."""

    id: UUID
    match_id: UUID
    storage_key: str
    original_filename: str
    content_type: str
    byte_size: int


@dataclass(frozen=True)
class ExtractedMetadata:
    """What the processor learned about the video. See `DecodedVideoMetadata`
    in `pickleball_api.models` for what each field means: display-orientation
    dimensions, an *average* frame rate, an estimated duration."""

    width: int
    height: int
    rotation_degrees: int
    average_fps: float
    duration_seconds: float
    frame_count: int
    #: None when the stream does not name its codec.
    codec: str | None


@dataclass(frozen=True)
class ProcessingResult:
    """A successful pass over a video, for the task layer to persist."""

    metadata: ExtractedMetadata
    #: A small record for the job log; never shown to a user.
    summary: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VideoProcessor(Protocol):
    """One pass over an uploaded video.

    Returning normally means the job succeeded, once the task layer has saved
    the result. Raising `JobFailure` fails it with a specific reason; any other
    exception fails it as an internal error and is logged with its traceback.
    """

    name: str
    version: str

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
        """Do the work and return what should be recorded."""


class MetadataProcessor:
    """Reads a video's container and stream metadata. No frame analysis.

    Failures map onto the existing user-safe codes. The detail passed to
    `JobFailure` is logged on the server only: it may name the storage key or
    carry the decoder's own message, neither of which belongs in a response.
    """

    name = "metadata"
    version = "1"

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
        report(JobStage.INGESTED, 0.0)
        try:
            stored = storage.size(video.storage_key)
            if stored != video.byte_size:
                # Truncated or replaced since upload: decoding it could
                # "succeed" on a different video from the one recorded.
                raise JobFailure(
                    JobErrorCode.UNREADABLE_VIDEO,
                    f"stored object is {stored} bytes, upload recorded {video.byte_size}",
                )
            with storage.local_path(video.storage_key) as path:
                report(JobStage.INGESTED, 0.25)
                decoded = read_metadata(path)
        except ObjectNotFound as exc:
            raise JobFailure(JobErrorCode.MISSING_VIDEO_FILE, "object not in storage") from exc
        except FileNotFoundError as exc:
            # Gone between the check and the decode.
            raise JobFailure(JobErrorCode.MISSING_VIDEO_FILE, "object vanished") from exc
        except VideoReadError as exc:
            raise JobFailure(JobErrorCode.UNREADABLE_VIDEO, str(exc)) from exc

        report(JobStage.INGESTED, 0.9)
        return ProcessingResult(
            metadata=ExtractedMetadata(
                width=decoded.width,
                height=decoded.height,
                rotation_degrees=decoded.rotation_degrees,
                average_fps=decoded.fps,
                duration_seconds=decoded.duration_seconds,
                frame_count=decoded.frame_count,
                codec=decoded.codec or None,
            ),
            summary={"processor": self.name, "processor_version": self.version},
        )


def default_processor() -> VideoProcessor:
    """The processor jobs run. One place to change when analysis stages land."""
    return MetadataProcessor()
