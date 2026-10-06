"""Video metadata and frame access via OpenCV.

Orientation: phone video is often stored sideways with a rotation tag in the
container. OpenCV's FFmpeg backend applies that tag by default
(`CAP_PROP_ORIENTATION_AUTO`), and when it does, both the frames and the
reported width and height are in **display orientation** -- the way the video
looks when played. This module switches auto-orientation on explicitly rather
than relying on the default, so every image coordinate in the pipeline is a
display-orientation pixel. `rotation_degrees` records the tag that was applied.
"""

import math
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]

#: The only rotations a container can meaningfully carry.
VALID_ROTATIONS = (0, 90, 180, 270)

#: Well past any real camera; a larger value is a broken header, not a video.
MAX_PLAUSIBLE_FPS = 1000.0


class VideoReadError(OSError):
    """The file exists but could not be opened or decoded as a video."""


class InvalidVideoMetadata(VideoReadError):
    """The video opened, but what it reports about itself is not usable."""


@dataclass(frozen=True)
class VideoMetadata:
    """What a video's container and stream report about it.

    `width` and `height` are in display orientation (see the module docstring).
    `fps` is the stream's **average** frame rate: phone video is often variable
    frame rate, so `frame_number / fps` is only an approximate timestamp, and
    `duration_seconds` (derived as `frame_count / fps`) is an estimate on the
    same terms. Exact timing comes from decoded frame timestamps.
    `codec` is the FourCC, or "" when the stream does not name one.
    """

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    codec: str
    rotation_degrees: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_metadata(path: Path) -> VideoMetadata:
    """Read and validate a video's metadata. Decodes no frames.

    Raises `FileNotFoundError` if there is no file, `VideoReadError` if it
    cannot be opened, and `InvalidVideoMetadata` if what it reports is
    implausible -- never a record full of zeros.
    """
    cap = _open(path)
    try:
        return metadata_from_properties(
            path,
            width=cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            height=cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
            fps=cap.get(cv2.CAP_PROP_FPS),
            frame_count=cap.get(cv2.CAP_PROP_FRAME_COUNT),
            fourcc=cap.get(cv2.CAP_PROP_FOURCC),
            rotation=cap.get(cv2.CAP_PROP_ORIENTATION_META),
        )
    except cv2.error as exc:
        raise VideoReadError(f"could not read metadata from {path}") from exc
    finally:
        cap.release()


def metadata_from_properties(
    path: Path,
    *,
    width: float,
    height: float,
    fps: float,
    frame_count: float,
    fourcc: float,
    rotation: float,
) -> VideoMetadata:
    """Validate OpenCV's raw property values into a `VideoMetadata`.

    Separate from `read_metadata` so the rules can be tested without a video
    file. OpenCV reports every property as a float, and reports 0 (or NaN)
    rather than failing when a container does not say.
    """
    int_width = _positive_int(width, "width")
    int_height = _positive_int(height, "height")
    int_frames = _positive_int(frame_count, "frame count")
    if not math.isfinite(fps) or not 0 < fps <= MAX_PLAUSIBLE_FPS:
        raise InvalidVideoMetadata(f"implausible frame rate {fps!r}")
    return VideoMetadata(
        path=str(path),
        width=int_width,
        height=int_height,
        fps=float(fps),
        frame_count=int_frames,
        duration_seconds=int_frames / fps,
        codec=fourcc_to_codec(fourcc),
        rotation_degrees=normalize_rotation(rotation),
    )


def normalize_rotation(degrees: float) -> int:
    """A rotation tag as one of 0, 90, 180 or 270.

    Containers and OpenCV versions disagree on sign and range (-90 and 270 are
    the same rotation), so the value is wrapped into [0, 360). Anything that is
    not a right angle is refused: no phone writes one, and a frame cannot be
    displayed at 45 degrees without resampling.
    """
    if not math.isfinite(degrees):
        raise InvalidVideoMetadata(f"rotation is not a number: {degrees!r}")
    rounded = round(degrees)
    if abs(degrees - rounded) > 1e-6:
        raise InvalidVideoMetadata(f"rotation is not a whole number of degrees: {degrees!r}")
    normalized = rounded % 360
    if normalized not in VALID_ROTATIONS:
        raise InvalidVideoMetadata(f"rotation is not a right angle: {degrees!r}")
    return normalized


def fourcc_to_codec(fourcc: float) -> str:
    """The four-character code as text, or "" if it is empty or not printable."""
    if not math.isfinite(fourcc) or fourcc <= 0:
        return ""
    value = int(fourcc)
    text = "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")
    return text if text.isascii() and text.isprintable() else ""


def _positive_int(value: float, name: str) -> int:
    if not math.isfinite(value) or value < 1 or value != int(value):
        raise InvalidVideoMetadata(f"implausible {name} {value!r}")
    return int(value)


def iter_frames(
    path: Path, start_frame: int = 0, end_frame: int | None = None, stride: int = 1
) -> Iterator[tuple[int, float, Frame]]:
    """Yield (frame_number, timestamp_ms, image) for frames in [start_frame, end_frame).

    Frame numbers always refer to the source video, so results link back to
    the original footage. Frames before start_frame are grabbed sequentially
    rather than seeked, which keeps frame numbering exact.
    """
    if stride < 1:
        raise ValueError("stride must be >= 1")
    cap = _open(path)
    try:
        frame_number = 0
        while frame_number < start_frame:
            if not cap.grab():
                return
            frame_number += 1
        while end_frame is None or frame_number < end_frame:
            if not cap.grab():
                return
            if (frame_number - start_frame) % stride == 0:
                timestamp_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                ok, image = cap.retrieve()
                if not ok:
                    return
                yield frame_number, timestamp_ms, cast(Frame, image)
            frame_number += 1
    finally:
        cap.release()


def read_frame(path: Path, frame_number: int) -> Frame:
    for _, _, image in iter_frames(path, start_frame=frame_number, end_frame=frame_number + 1):
        return image
    raise ValueError(f"frame {frame_number} is beyond the end of {path}")


def _open(path: Path) -> cv2.VideoCapture:
    if not path.exists():
        raise FileNotFoundError(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoReadError(f"could not open video {path}")
    # Here rather than per caller, so metadata and frames always agree on
    # orientation (see the module docstring).
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    return cap
