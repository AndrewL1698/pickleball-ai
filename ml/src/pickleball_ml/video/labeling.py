"""Pieces shared by the interactive OpenCV labeling tools."""

from pathlib import Path

import cv2
import numpy as np

from pickleball_ml.video.reader import Frame, iter_frames


def load_frames(video: Path, frames: list[int]) -> dict[int, Frame]:
    """The requested frames of `video`, decoded in one pass."""
    wanted = set(frames)
    images: dict[int, Frame] = {}
    for frame_number, _, image in iter_frames(video, min(frames), max(frames) + 1):
        if frame_number in wanted:
            images[frame_number] = image
    return images


def draw_zoom_inset(image: Frame, cursor: tuple[int, int], zoom: int, size: int) -> None:
    """Magnified view around the cursor, drawn in place in the corner away from it."""
    height, width = image.shape[:2]
    half = size // (2 * zoom)
    x = int(np.clip(cursor[0], half, width - half - 1))
    y = int(np.clip(cursor[1], half, height - half - 1))
    patch = image[y - half:y + half, x - half:x + half]
    if patch.size == 0:
        return
    zoomed = cv2.resize(patch, (size * 2, size * 2), interpolation=cv2.INTER_NEAREST)
    centre = size
    cv2.line(zoomed, (centre, centre - 14), (centre, centre + 14), (0, 0, 255), 1)
    cv2.line(zoomed, (centre - 14, centre), (centre + 14, centre), (0, 0, 255), 1)
    top = height - zoomed.shape[0] if cursor[1] < height // 2 else 0
    left = 0 if cursor[0] > width // 2 else width - zoomed.shape[1]
    image[top:top + zoomed.shape[0], left:left + zoomed.shape[1]] = zoomed
    cv2.rectangle(image, (left, top),
                  (left + zoomed.shape[1] - 1, top + zoomed.shape[0] - 1), (255, 255, 255), 1)
