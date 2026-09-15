"""Cheap clothing-color appearance descriptor for linking a player's track fragments."""

import cv2
import numpy as np
from numpy.typing import NDArray

from pickleball_ml.video.reader import Frame

HIST_BINS = (8, 3, 3)  # hue, saturation, value
DESCRIPTOR_SIZE = int(np.prod(HIST_BINS))

Descriptor = NDArray[np.float32]


def torso_histogram(image: Frame, x1: float, y1: float, x2: float, y2: float) -> Descriptor | None:
    """Normalized HSV histogram of the torso region of a person box.

    Uses the middle of the box (20-55% of height, central half of width), which
    is mostly shirt and least affected by the net, paddle, and floor.
    """
    height, width = image.shape[:2]
    box_w, box_h = x2 - x1, y2 - y1
    left = max(0, round(x1 + 0.25 * box_w))
    right = min(width, round(x2 - 0.25 * box_w))
    top = max(0, round(y1 + 0.20 * box_h))
    bottom = min(height, round(y1 + 0.55 * box_h))
    if right - left < 2 or bottom - top < 2:
        return None
    hsv = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BINS), [0, 180, 0, 256, 0, 256])
    total = float(hist.sum())
    if total <= 0:
        return None
    return (hist.ravel() / total).astype(np.float32)


def mean_descriptor(descriptors: list[Descriptor]) -> Descriptor | None:
    if not descriptors:
        return None
    mean = np.mean(np.stack(descriptors), axis=0)
    return (mean / mean.sum()).astype(np.float32)


def appearance_distance(a: Descriptor | None, b: Descriptor | None) -> float:
    """Bhattacharyya distance in [0, 1]; 0.5 (uninformative) when either is missing."""
    if a is None or b is None:
        return 0.5
    coefficient = float(np.sum(np.sqrt(a.astype(np.float64) * b.astype(np.float64))))
    return float(np.sqrt(max(0.0, 1.0 - min(coefficient, 1.0))))
