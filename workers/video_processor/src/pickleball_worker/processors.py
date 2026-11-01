"""What actually gets done to a video, behind one interface.

The default processor reads the video's metadata -- dimensions, rotation,
average frame rate, duration, frame count, codec -- and writes the stills a
person places calibration landmarks on, with `pickleball_ml.video.reader`. That
is all it does: nothing in a frame is analysed, and no model is loaded. Only the
lightweight video module is imported, never anything that pulls in torch or YOLO
weights.

A processor is handed a `VideoRef`, not the ORM object, and returns a typed
`ProcessingResult`. It works through `Storage` and reports progress through a
callback, so it cannot reach the database: the task layer persists the result,
in the same transaction as the job's terminal status, and deletes any files the
result names if that fails. A future version running on a separate GPU host does
not have to change shape.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import JobStage, ReferenceFrameKind
from pickleball_api.storage import ObjectNotFound, Storage, new_storage_key
from pickleball_ml.video.reader import (
    Frame,
    VideoReadError,
    encode_jpeg,
    median_background,
    read_metadata,
    sample_frames,
)

#: Frames in a calibration background, spread across the video. Enough for a
#: player who stays put for a while to vanish, and few enough to hold in memory
#: at 4K. The median needs the frames and a working copy, about 1.2 GB at 4K.
BACKGROUND_SAMPLES = 25

#: How many of those are also offered as stills, for a video whose background
#: is unusable because the camera moved.
SAMPLE_STILLS = 3

#: Far past any JPEG these can make; the limit is only there because `write` asks.
MAX_STILL_BYTES = 64 * 1024 * 1024

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
class StoredStill:
    """A calibration image the processor has already written to storage."""

    storage_key: str
    kind: ReferenceFrameKind
    #: Null for a background, which comes from no single moment.
    position_seconds: float | None
    source_frame_count: int
    width: int
    height: int
    byte_size: int


@dataclass(frozen=True)
class ProcessingResult:
    """A successful pass over a video, for the task layer to persist."""

    metadata: ExtractedMetadata
    #: Already in storage. The task layer records them, or deletes them.
    stills: tuple[StoredStill, ...] = ()
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


class CalibrationPreparationProcessor:
    """Metadata, then the stills a person calibrates the court on.

    Every job does both, so a match that has finished processing can always be
    calibrated. A video that yields metadata but not one decodable frame is
    `unreadable_video`: it could never be calibrated or analysed.
    """

    name = "calibration_preparation"
    version = "1"

    def process(
        self, video: VideoRef, storage: Storage, report: ProgressReporter
    ) -> ProcessingResult:
        metadata = (
            MetadataProcessor()
            .process(video, storage, lambda stage, done: report(stage, done * 0.1))
            .metadata
        )
        report(JobStage.INGESTED, 0.1)
        frames = _decode_samples(video, storage, metadata.duration_seconds, report)
        report(JobStage.INGESTED, 0.8)

        images = _stills_from(frames)
        stored: list[StoredStill] = []
        try:
            for kind, position, count, image in images:
                stored.append(_store_still(storage, kind, position, count, image))
        except BaseException:
            for still in stored:
                storage.delete(still.storage_key)
            raise
        report(JobStage.INGESTED, 0.95)
        return ProcessingResult(
            metadata=metadata,
            stills=tuple(stored),
            summary={
                "processor": self.name,
                "processor_version": self.version,
                "frames_decoded": len(frames),
            },
        )


def _decode_samples(
    video: VideoRef, storage: Storage, duration_seconds: float, report: ProgressReporter
) -> list[tuple[float, Frame]]:
    """Frames spread evenly through the video, each at the middle of its share."""
    seconds = [
        duration_seconds * (index + 0.5) / BACKGROUND_SAMPLES
        for index in range(BACKGROUND_SAMPLES)
    ]
    frames: list[tuple[float, Frame]] = []
    try:
        with storage.local_path(video.storage_key) as path:
            for position, image in sample_frames(path, seconds):
                frames.append((position, image))
                report(JobStage.INGESTED, 0.1 + 0.7 * len(frames) / BACKGROUND_SAMPLES)
    except ObjectNotFound as exc:
        raise JobFailure(JobErrorCode.MISSING_VIDEO_FILE, "object not in storage") from exc
    except FileNotFoundError as exc:
        raise JobFailure(JobErrorCode.MISSING_VIDEO_FILE, "object vanished") from exc
    except VideoReadError as exc:
        raise JobFailure(JobErrorCode.UNREADABLE_VIDEO, str(exc)) from exc
    if not frames:
        raise JobFailure(JobErrorCode.UNREADABLE_VIDEO, "no frame could be decoded")
    return frames


def _stills_from(
    frames: list[tuple[float, Frame]],
) -> list[tuple[ReferenceFrameKind, float | None, int, Frame]]:
    """The background first, then stills from around the quarter points."""
    picks = sorted({len(frames) * (i + 1) // (SAMPLE_STILLS + 1) for i in range(SAMPLE_STILLS)})
    background = median_background([image for _, image in frames])
    return [
        (ReferenceFrameKind.BACKGROUND, None, len(frames), background),
        *((ReferenceFrameKind.SAMPLE, frames[i][0], 1, frames[i][1]) for i in picks),
    ]


def _store_still(
    storage: Storage,
    kind: ReferenceFrameKind,
    position_seconds: float | None,
    source_frame_count: int,
    image: Frame,
) -> StoredStill:
    content = encode_jpeg(image)
    key = new_storage_key(".jpg")
    size = storage.write(key, [content], max_bytes=MAX_STILL_BYTES)
    height, width = image.shape[:2]
    return StoredStill(
        storage_key=key,
        kind=kind,
        position_seconds=position_seconds,
        source_frame_count=source_frame_count,
        width=width,
        height=height,
        byte_size=size,
    )


def default_processor() -> VideoProcessor:
    """The processor jobs run. One place to change when analysis stages land."""
    return CalibrationPreparationProcessor()
