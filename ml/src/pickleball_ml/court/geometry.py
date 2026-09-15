"""Planar homography between image pixels and court coordinates."""

import cv2
import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]


class CalibrationError(ValueError):
    """Raised when calibration points cannot produce a valid homography."""


def fit_homography(image_points: ArrayLike, court_points: ArrayLike) -> FloatArray:
    """Least-squares homography mapping image pixels (x, y) to court feet (X, Y).

    Uses every point (no RANSAC): manual clicks have noise, not outliers.
    """
    img = _as_points(image_points)
    court = _as_points(court_points)
    if len(img) != len(court):
        raise CalibrationError(f"got {len(img)} image points but {len(court)} court points")
    if len(img) < 4:
        raise CalibrationError(f"need at least 4 points, got {len(img)}")
    if _all_collinear(court) or _all_collinear(img):
        raise CalibrationError("points are collinear")

    homography, _ = cv2.findHomography(img, court, method=0)
    if homography is None:
        raise CalibrationError("homography fit failed (degenerate point configuration)")
    return np.asarray(homography, dtype=np.float64)


def apply_homography(homography: ArrayLike, points: ArrayLike) -> FloatArray:
    """Map (N, 2) points through a 3x3 homography. Points at infinity become NaN."""
    h = np.asarray(homography, dtype=np.float64)
    pts = _as_points(points)
    homogeneous = np.hstack([pts, np.ones((len(pts), 1))]) @ h.T
    w = homogeneous[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = homogeneous[:, :2] / w
    out[np.abs(w[:, 0]) < 1e-12] = np.nan
    return out


def reprojection_errors(
    homography: ArrayLike, image_points: ArrayLike, court_points: ArrayLike
) -> FloatArray:
    """Per-point distance in feet between projected image points and true court points.

    With exactly 4 points the fit is exact, so errors are ~0 and say nothing
    about calibration quality. Use more points for a meaningful check.
    """
    projected = apply_homography(homography, image_points)
    return np.asarray(np.linalg.norm(projected - _as_points(court_points), axis=1))


def _as_points(points: ArrayLike) -> FloatArray:
    arr = np.asarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise CalibrationError(f"expected (N, 2) points, got shape {arr.shape}")
    return arr


def _all_collinear(points: FloatArray) -> bool:
    centered = points - points.mean(axis=0)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    return bool(singular_values[1] <= 1e-6 * max(singular_values[0], 1.0))
