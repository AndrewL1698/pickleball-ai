"""Debug video for ball tracking: chosen position, confidence, and a short trail."""

from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.video.reader import Frame, iter_frames

CHOSEN = (60, 245, 210)
REJECTED = (140, 140, 140)
INTERPOLATED = (0, 190, 255)


def render_ball_video(
    video: Path,
    output: Path,
    track: pd.DataFrame,
    candidates: pd.DataFrame,
    start_frame: int,
    end_frame: int,
    stride: int,
    fps: float,
    trail_seconds: float = 1.0,
    progress: Callable[[int], None] | None = None,
) -> None:
    """Source frames with the tracked ball, the rejected candidates, and its recent path."""
    columns = ["frame_number", "x", "y", "score", "visible", "interpolated"]
    track_by_frame = {int(row[0]): row for row in track[columns].to_numpy(dtype=float)}
    candidates_by_frame = {
        cast(int, f): g[["x", "y", "candidate"]].to_numpy(dtype=float)
        for f, g in candidates.groupby("frame_number")
    }
    trail: deque[tuple[int, int]] = deque(maxlen=max(1, round(trail_seconds * fps / stride)))
    writer: cv2.VideoWriter | None = None
    try:
        for frame_number, timestamp_ms, image in iter_frames(video, start_frame, end_frame,
                                                             stride):
            out = image.copy()
            row = track_by_frame.get(frame_number)
            chosen_xy = None
            if row is not None and bool(row[4]) and np.isfinite(row[1]):
                chosen_xy = (round(float(row[1])), round(float(row[2])))
            for x, y, candidate in candidates_by_frame.get(frame_number, np.empty((0, 3))):
                if int(candidate) < 0 or not np.isfinite(x):
                    continue
                point = (round(float(x)), round(float(y)))
                if chosen_xy is not None and point == chosen_xy:
                    continue
                cv2.circle(out, point, 9, REJECTED, 1)
            if chosen_xy is not None and row is not None:
                interpolated = bool(row[5])
                color = INTERPOLATED if interpolated else CHOSEN
                trail.append(chosen_xy)
                cv2.circle(out, chosen_xy, 14, color, 2)
                score = float(row[3])
                text = "interpolated" if interpolated else f"{score:.2f}"
                cv2.putText(out, text, (chosen_xy[0] + 18, chosen_xy[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
            elif row is not None:
                cv2.putText(out, "no ball", (out.shape[1] - 180, 40), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, REJECTED, 2, cv2.LINE_AA)
            if len(trail) >= 2:
                cv2.polylines(out, [np.array(trail, dtype=np.int32)], False, CHOSEN, 2,
                              cv2.LINE_AA)
            _label(out, f"frame {frame_number}  t={timestamp_ms / 1000:.2f}s")
            if writer is None:
                writer = _open_writer(output, fps / stride, out.shape[1], out.shape[0])
            writer.write(out)
            if progress is not None:
                progress(frame_number)
    finally:
        if writer is not None:
            writer.release()


def _label(image: Frame, text: str) -> None:
    cv2.rectangle(image, (0, 0), (420, 32), (0, 0, 0), -1)
    cv2.putText(image, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1,
                cv2.LINE_AA)


def _open_writer(path: Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    for codec in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*codec), fps,
                                 (width, height))
        if writer.isOpened():
            return writer
        writer.release()
    raise OSError(f"could not open a video writer for {path}")
