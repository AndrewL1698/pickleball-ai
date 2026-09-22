"""Image-space region where our court's ball can be.

Adjacent courts are the dominant false positive for a ball detector: a tennis
model finds every ball in the frame, and the neighbouring game's ball is just as
ball-like as ours. Player detections are gated by projecting the feet onto the
court plane, but that trick does not work for the ball: the ball is airborne, so
its projection through the ground-plane homography lands nowhere meaningful.

Instead the gate is a polygon in the image: the court quadrilateral (plus a
margin in feet, projected through the calibration) with a cap added above its
far edge for balls in the air. The cap follows the far edge rather than the
near edge, because the near edge spans almost the full frame width in a
behind-baseline view and a cap that wide would re-admit the neighbouring
courts, which appear at the same image height as our far court.

Consequence, and a known limitation: a ball lofted high over the *near* half
can leave the gate sideways. Those frames are simply not tracked rather than
tracked wrongly, which is the trade this stage prefers.

The gate only ever marks candidates; nothing is dropped from the raw detector
output, so a different gate can be applied later without re-running inference.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from pickleball_ml.court.calibration import Calibration
from pickleball_ml.court.geometry import FloatArray, apply_homography
from pickleball_ml.court.spec import HALF_LENGTH_FT, HALF_WIDTH_FT


@dataclass(frozen=True)
class BallGate:
    """How far around the court a ball may appear, before lifting the top edge."""

    side_margin_ft: float = 5.0
    near_end_margin_ft: float = 6.0
    far_end_margin_ft: float = 5.0
    # Height of the cap above the far edge, as a fraction of the court's on-screen height.
    up_fraction: float = 0.8
    # How much wider than the far edge the cap is, on each side.
    cap_widen: float = 0.35
    # Extension below the near edge, for a ball played in front of a near player.
    down_fraction: float = 0.15


def gate_polygon(calibration: Calibration, gate: BallGate) -> FloatArray:
    """Image-space polygon of the gate, as (N, 2) pixels."""
    inverse = np.linalg.inv(calibration.homography)
    w = HALF_WIDTH_FT + gate.side_margin_ft
    near = -HALF_LENGTH_FT - gate.near_end_margin_ft
    far = HALF_LENGTH_FT + gate.far_end_margin_ft
    corners = apply_homography(inverse, np.array([[-w, near], [w, near], [w, far], [-w, far]]))
    if not np.all(np.isfinite(corners)):
        raise ValueError("court corners do not project into the image")
    near_left, near_right, far_right, far_left = corners
    height = float(abs(np.mean([near_left[1], near_right[1]])
                       - np.mean([far_left[1], far_right[1]])))
    up = gate.up_fraction * height
    down = gate.down_fraction * height
    far_width = float(far_right[0] - far_left[0])
    widen = gate.cap_widen * far_width
    return np.array([
        [near_left[0], near_left[1] + down],
        [near_right[0], near_right[1] + down],
        [far_right[0], far_right[1]],
        [far_right[0] + widen, far_right[1] - up],
        [far_left[0] - widen, far_left[1] - up],
        [far_left[0], far_left[1]],
    ])


def inside(polygon: FloatArray, points: FloatArray) -> np.ndarray:
    """Which (N, 2) image points fall inside the polygon (edges count as inside)."""
    contour = np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    result = np.zeros(len(pts), dtype=bool)
    for index, (x, y) in enumerate(pts):
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        result[index] = cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0
    return result
