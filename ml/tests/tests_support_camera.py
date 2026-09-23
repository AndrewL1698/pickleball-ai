"""Shared helper: a court calibration from a synthetic pinhole camera."""

import cv2
import numpy as np

from pickleball_ml.court.calibration import Calibration
from pickleball_ml.court.spec import LANDMARKS

WIDTH, HEIGHT = 1920, 1080


def synthetic_calibration(
    position: tuple[float, float, float], focal: float, target: tuple[float, float] = (0.0, 0.0)
) -> Calibration:
    """Project the court landmarks through a pinhole camera at `position` looking at `target`."""
    eye = np.array(position, dtype=float)
    forward = np.array([target[0], target[1], 0.0]) - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.vstack([right, down, forward])  # rows: camera x, y, z axes in court frame
    rvec, _ = cv2.Rodrigues(rotation)
    tvec = -rotation @ eye
    k = np.array([[focal, 0, WIDTH / 2], [0, focal, HEIGHT / 2], [0, 0, 1]], dtype=float)
    court = np.array([[*xy, 0.0] for xy in LANDMARKS.values()])
    image, _ = cv2.projectPoints(court, rvec, tvec, k, None)
    points = {name: (float(x), float(y)) for name, (x, y) in
              zip(LANDMARKS, image.reshape(-1, 2), strict=True)}
    return Calibration.fit("synthetic.mp4", 0, points)
