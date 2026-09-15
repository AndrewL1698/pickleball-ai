"""Video metadata and frame access via OpenCV."""

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]


@dataclass(frozen=True)
class VideoMetadata:
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
    cap = _open(path)
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        return VideoMetadata(
            path=str(path),
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=fps,
            frame_count=frame_count,
            duration_seconds=frame_count / fps if fps > 0 else 0.0,
            codec="".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00"),
            rotation_degrees=int(cap.get(cv2.CAP_PROP_ORIENTATION_META)),
        )
    finally:
        cap.release()


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
        raise OSError(f"could not open video {path}")
    return cap
