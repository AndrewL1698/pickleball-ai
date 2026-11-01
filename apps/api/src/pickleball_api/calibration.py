"""Turning landmarks placed in the browser into a court calibration.

The maths is `pickleball_ml.court`, the same code the CLI calibrates with, so
there is one homography implementation. Only the geometry and the court
specification are imported from it: OpenCV and NumPy, never a model library.

A refusal carries a fixed sentence written here. The geometry's own messages
are for developers, and nothing from a client is echoed back.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from pickleball_ml.court.geometry import (
    CalibrationError,
    apply_homography,
    fit_homography,
    reprojection_errors,
)
from pickleball_ml.court.spec import (
    HALF_LENGTH_FT,
    HALF_WIDTH_FT,
    LANDMARKS,
    court_lines,
)

#: Fewer points than this are an exact fit, whose error is always about zero.
EXACT_FIT_POINTS: Final = 4

# Worst seen across the Phase 0 cameras, all carefully placed, was 0.35 ft mean
# and 0.81 ft max (`docs/PHASE_0B_RESULTS.md`), so these are past a careful fit.
REVIEW_MEAN_ERROR_FT: Final = 0.5
REVIEW_MAX_ERROR_FT: Final = 1.0


class CourtLandmark(enum.StrEnum):
    """The painted intersections a calibration can use.

    Written out, rather than built from `LANDMARKS`, so the API schema names them
    and the web app's copy can be checked against it. A test keeps the two equal.
    """

    NEAR_LEFT_BASELINE_CORNER = "near_left_baseline_corner"
    NEAR_CENTER_BASELINE = "near_center_baseline"
    NEAR_RIGHT_BASELINE_CORNER = "near_right_baseline_corner"
    NEAR_LEFT_KITCHEN = "near_left_kitchen"
    NEAR_CENTER_KITCHEN = "near_center_kitchen"
    NEAR_RIGHT_KITCHEN = "near_right_kitchen"
    FAR_LEFT_KITCHEN = "far_left_kitchen"
    FAR_CENTER_KITCHEN = "far_center_kitchen"
    FAR_RIGHT_KITCHEN = "far_right_kitchen"
    FAR_LEFT_BASELINE_CORNER = "far_left_baseline_corner"
    FAR_CENTER_BASELINE = "far_center_baseline"
    FAR_RIGHT_BASELINE_CORNER = "far_right_baseline_corner"


class CalibrationRejected(Exception):
    """These landmarks cannot make a usable calibration. `detail` is safe to show."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class FittedCalibration:
    image_points: dict[str, list[float]]
    court_points: dict[str, list[float]]
    homography: list[list[float]]
    reprojection_error_ft: dict[str, float]
    mean_reprojection_error_ft: float
    max_reprojection_error_ft: float


def fit_calibration(
    image_points: Mapping[str, tuple[float, float]], *, width: int, height: int
) -> FittedCalibration:
    """Fit a homography to landmarks on a `width` by `height` image, or refuse."""
    if len(image_points) < EXACT_FIT_POINTS:
        raise CalibrationRejected(f"Place at least {EXACT_FIT_POINTS} landmarks.")
    if any(not (0 <= x <= width and 0 <= y <= height) for x, y in image_points.values()):
        raise CalibrationRejected("Every landmark has to be inside the image.")

    # A fixed order, whatever order the request listed them in.
    names = [name for name in LANDMARKS if name in image_points]
    image = np.array([image_points[name] for name in names], dtype=np.float64)
    court = np.array([LANDMARKS[name] for name in names], dtype=np.float64)
    try:
        homography = fit_homography(image, court)
        errors = reprojection_errors(homography, image, court)
        inverse = np.linalg.inv(homography)
    except (CalibrationError, np.linalg.LinAlgError) as exc:
        raise CalibrationRejected(
            "These landmarks do not make a usable court. Spread them across the court, "
            "not along one line."
        ) from exc
    if not (np.all(np.isfinite(homography)) and np.all(np.isfinite(errors))):
        raise CalibrationRejected("These landmarks do not make a usable court. Check each one.")
    if _is_mirrored(inverse):
        raise CalibrationRejected(
            "Left and right look swapped. Left and right are as seen from the near baseline, "
            "facing the net."
        )
    return FittedCalibration(
        image_points={name: [float(image_points[name][0]), float(image_points[name][1])]
                      for name in names},
        court_points={name: [LANDMARKS[name][0], LANDMARKS[name][1]] for name in names},
        homography=homography.tolist(),
        reprojection_error_ft={
            name: float(error) for name, error in zip(names, errors, strict=True)
        },
        mean_reprojection_error_ft=float(np.mean(errors)),
        max_reprojection_error_ft=float(np.max(errors)),
    )


def _is_mirrored(court_to_image: np.ndarray) -> bool:
    """True when the court comes out reflected: a fit no real camera could produce.

    Any camera above the court sees near-left, near-right, far-right, far-left
    run counter-clockwise, which is a negative area with image Y pointing down.
    A swapped left and right fits just as well, so the error cannot catch it.
    """
    corners = np.array(
        [
            (-HALF_WIDTH_FT, -HALF_LENGTH_FT),
            (HALF_WIDTH_FT, -HALF_LENGTH_FT),
            (HALF_WIDTH_FT, HALF_LENGTH_FT),
            (-HALF_WIDTH_FT, HALF_LENGTH_FT),
        ]
    )
    x, y = apply_homography(court_to_image, corners).T
    return bool(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)) > 0)


def projected_court_lines(homography: list[list[float]]) -> dict[str, list[tuple[float, float]]]:
    """Each court line's two ends in image pixels.

    The ends are enough, because a homography keeps straight lines straight. A
    line with an end the camera could not see from this plane is left out.
    """
    inverse = np.linalg.inv(np.array(homography, dtype=np.float64))
    lines: dict[str, list[tuple[float, float]]] = {}
    for name, segment in court_lines().items():
        ends = apply_homography(inverse, np.array(segment, dtype=np.float64))
        if np.all(np.isfinite(ends)):
            lines[name] = [(float(x), float(y)) for x, y in ends]
    return lines


def calibration_warnings(
    court_points: Mapping[str, tuple[float, float]], mean_error_ft: float, max_error_ft: float
) -> list[str]:
    """What a person should review about a saved calibration, in fixed sentences."""
    warnings: list[str] = []
    if len(court_points) <= EXACT_FIT_POINTS:
        warnings.append(
            "Four landmarks always fit exactly, so the error says nothing about this "
            "calibration. Place more if you can."
        )
    if mean_error_ft > REVIEW_MEAN_ERROR_FT or max_error_ft > REVIEW_MAX_ERROR_FT:
        warnings.append(
            "The landmarks disagree more than a careful calibration does. Check the ones "
            "with the largest error, and that the video was recorded with the main lens."
        )
    sides = {y > 0 for _, y in court_points.values()}
    if len(sides) < 2:
        warnings.append(
            "All the landmarks are on one half of the court, so positions on the other "
            "half are extrapolated."
        )
    return warnings
