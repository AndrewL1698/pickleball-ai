"""Camera characteristics recovered from a court calibration and the footage.

Placement: a ground-plane homography plus a pinhole camera model (square pixels,
principal point at the image center, no lens distortion) determines the focal
length and the camera pose. From the pose we report where the camera stands in
court coordinates: lateral offset from the center line, distance behind the near
baseline, and height. These are estimates: lens distortion, click noise, and
digital cropping in edited footage all bias them, so they are for comparing
recording setups, not survey-grade measurements.

Stability: sampled frames are matched to a reference frame with ORB features and
a RANSAC homography; the reported drift is how far points in the lower-middle
of the image move. Jumps between consecutive samples locate camera bumps.

Sharpness: variance of the Laplacian inside the projected court area, a common
blur/detail proxy. Only comparable between videos of the same resolution.
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np

from pickleball_ml.court.calibration import Calibration
from pickleball_ml.court.geometry import FloatArray, apply_homography
from pickleball_ml.court.spec import HALF_LENGTH_FT, HALF_WIDTH_FT, LANDMARKS
from pickleball_ml.video.reader import Frame

MIN_PLAUSIBLE_HFOV_DEG = 20.0
MAX_PLAUSIBLE_HFOV_DEG = 130.0


def focal_from_homography(image_to_court: FloatArray, width: int, height: int) -> float | None:
    """Focal length in pixels from the two orthonormality constraints on the rotation.

    With W = inverse(H) mapping court (X, Y, 1) to image pixels and the principal point
    moved to the origin, W ~ K [r1 r2 t] with K = diag(f, f, 1). r1 . r2 = 0 and
    |r1| = |r2| each give one linear equation in 1/f^2; they are combined by least
    squares. Returns None when the geometry does not constrain f (e.g. image plane
    parallel to the court) or the solution is not positive.
    """
    shift = np.array([[1.0, 0.0, -width / 2.0], [0.0, 1.0, -height / 2.0], [0.0, 0.0, 1.0]])
    w = shift @ np.linalg.inv(image_to_court)
    w = w / np.linalg.norm(w[:, :2])
    (a1, b1, c1), (a2, b2, c2) = w[:, 0], w[:, 1]
    # Each row: coefficient * (1/f^2) + constant = 0.
    rows = np.array([
        [a1 * a2 + b1 * b2, c1 * c2],
        [a1 * a1 + b1 * b1 - a2 * a2 - b2 * b2, c1 * c1 - c2 * c2],
    ])
    denominator = float(np.sum(rows[:, 0] ** 2))
    if denominator < 1e-18:
        return None
    inverse_f2 = -float(np.sum(rows[:, 0] * rows[:, 1])) / denominator
    if inverse_f2 <= 0:
        return None
    return float(1.0 / np.sqrt(inverse_f2))


@dataclass(frozen=True)
class CameraPlacement:
    focal_px: float
    focal_source: str  # "homography" or "assumed"
    horizontal_fov_deg: float
    lateral_offset_ft: float  # camera X; 0 = behind the center line, + = right
    behind_near_baseline_ft: float  # distance from the near baseline, along -Y
    height_ft: float
    pitch_down_deg: float  # angle of the optical axis below horizontal
    yaw_deg: float  # optical axis angle from the court's long axis, + = toward +X

    def to_dict(self) -> dict[str, Any]:
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(self).items()}


def camera_placement(
    calibration: Calibration, width: int, height: int, assumed_hfov_deg: float = 70.0
) -> CameraPlacement:
    """Camera position and orientation in court coordinates (feet).

    Uses the focal length recovered from the homography when it is plausible, otherwise
    a typical phone main-lens field of view.
    """
    focal = focal_from_homography(calibration.homography, width, height)
    source = "homography"
    if focal is None or not (MIN_PLAUSIBLE_HFOV_DEG <= _hfov(focal, width)
                             <= MAX_PLAUSIBLE_HFOV_DEG):
        focal = width / 2.0 / np.tan(np.radians(assumed_hfov_deg) / 2.0)
        source = "assumed"
    k = np.array([[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]])
    names = list(calibration.image_points)
    image = np.array([calibration.image_points[n] for n in names], dtype=np.float64)
    court = np.array([[*LANDMARKS[n], 0.0] for n in names], dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(court, image, k, None, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError("camera pose estimation failed")
    rotation, _ = cv2.Rodrigues(rvec)
    center = (-rotation.T @ tvec).ravel()
    if center[2] < 0:
        # Court Z is up for the (X right, Y away from camera) axes used here; a solution
        # below the ground is the mirror image of the real one.
        raise ValueError("camera pose estimation placed the camera below the court")
    axis = rotation.T @ np.array([0.0, 0.0, 1.0])  # optical axis in court coordinates
    return CameraPlacement(
        focal_px=float(focal),
        focal_source=source,
        horizontal_fov_deg=_hfov(float(focal), width),
        lateral_offset_ft=float(center[0]),
        behind_near_baseline_ft=float(-HALF_LENGTH_FT - center[1]),
        height_ft=float(center[2]),
        pitch_down_deg=float(np.degrees(np.arcsin(-axis[2] / np.linalg.norm(axis)))),
        yaw_deg=float(np.degrees(np.arctan2(axis[0], axis[1]))),
    )


def _hfov(focal: float, width: int) -> float:
    return float(np.degrees(2.0 * np.arctan(width / 2.0 / focal)))


def court_visibility(calibration: Calibration, width: int, height: int) -> dict[str, Any]:
    """Which court corners project inside the frame, and how many pixels the court spans."""
    inverse = np.linalg.inv(calibration.homography)
    corners = {
        "near_left": (-HALF_WIDTH_FT, -HALF_LENGTH_FT),
        "near_right": (HALF_WIDTH_FT, -HALF_LENGTH_FT),
        "far_left": (-HALF_WIDTH_FT, HALF_LENGTH_FT),
        "far_right": (HALF_WIDTH_FT, HALF_LENGTH_FT),
    }
    projected = apply_homography(inverse, np.array(list(corners.values())))
    inside = {name: bool(0 <= x < width and 0 <= y < height)
              for name, (x, y) in zip(corners, projected, strict=True)}
    near_px = float(np.hypot(*(projected[1] - projected[0])))
    far_px = float(np.hypot(*(projected[3] - projected[2])))
    return {
        "corners_in_frame": inside,
        "near_baseline_px": round(near_px, 1),
        "far_baseline_px": round(far_px, 1),
        # How much smaller far-court objects appear than near-court ones.
        "far_to_near_scale": round(far_px / near_px, 3) if near_px > 0 else None,
        "court_area_fraction": round(_court_area_px(projected) / (width * height), 3),
    }


def _court_area_px(corners: FloatArray) -> float:
    polygon = corners[[0, 1, 3, 2]].astype(np.float32)
    return float(abs(cv2.contourArea(polygon)))


def court_sharpness(image: Frame, calibration: Calibration) -> float:
    """Variance of the Laplacian inside the projected court (plus a margin)."""
    inverse = np.linalg.inv(calibration.homography)
    w, h = HALF_WIDTH_FT + 2.0, HALF_LENGTH_FT + 2.0
    polygon = apply_homography(inverse, np.array([[-w, -h], [w, -h], [w, h], [-w, h]]))
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(polygon).astype(np.int32)], 255)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    values = laplacian[mask > 0]
    return float(values.var()) if values.size else 0.0


@dataclass(frozen=True)
class DriftSample:
    time_s: float
    drift_px: float | None  # displacement relative to the reference frame
    step_px: float | None  # displacement relative to the previous sample


def camera_drift(
    frames: Iterable[tuple[float, Frame]], progress: Callable[[int], None] | None = None
) -> list[DriftSample]:
    """Image displacement of each sampled frame relative to the first one and to the
    previous one. Frames must be in time order."""
    orb = cv2.ORB.create(3000)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    reference: tuple[Any, Any, float] | None = None
    previous: tuple[Any, Any, float] | None = None
    samples = []
    for index, (time_s, image) in enumerate(frames):
        scale = 960.0 / image.shape[1]
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        keypoints, descriptors = orb.detectAndCompute(gray, None)
        current = (keypoints, descriptors, scale)
        if reference is None:
            reference = current
            samples.append(DriftSample(time_s, 0.0, None))
        else:
            probe = _probe_points(small.shape[1], small.shape[0])
            drift = _displacement(matcher, reference, current, probe)
            step = _displacement(matcher, previous, current, probe) if previous else None
            samples.append(DriftSample(time_s, drift, step))
        previous = current
        if progress is not None:
            progress(index)
    return samples


def _probe_points(width: int, height: int) -> FloatArray:
    """Points around the court area (lower middle of a behind-baseline view)."""
    return np.array([[0.25 * width, 0.75 * height], [0.75 * width, 0.75 * height],
                     [0.5 * width, 0.55 * height], [0.5 * width, 0.9 * height]])


def _displacement(
    matcher: cv2.BFMatcher, a: tuple[Any, Any, float], b: tuple[Any, Any, float],
    probe: FloatArray,
) -> float | None:
    if a[1] is None or b[1] is None:
        return None
    matches = matcher.match(a[1], b[1])
    if len(matches) < 20:
        return None
    src = np.array([a[0][m.queryIdx].pt for m in matches], dtype=np.float64)
    dst = np.array([b[0][m.trainIdx].pt for m in matches], dtype=np.float64)
    homography, _ = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if homography is None:
        return None
    moved = apply_homography(homography, probe)
    return float(np.max(np.linalg.norm(moved - probe, axis=1)) / a[2])


def summarize_drift(samples: list[DriftSample], jump_px: float = 20.0) -> dict[str, Any]:
    drift = np.array([s.drift_px for s in samples if s.drift_px is not None], dtype=float)
    jumps = [{"time_s": round(s.time_s, 1), "step_px": round(s.step_px, 1)} for s in samples
             if s.step_px is not None and s.step_px > jump_px]
    return {
        "samples": len(samples),
        "unmatched_samples": sum(s.drift_px is None for s in samples),
        "median_drift_px": round(float(np.median(drift)), 1) if drift.size else None,
        "p95_drift_px": round(float(np.percentile(drift, 95)), 1) if drift.size else None,
        "max_drift_px": round(float(np.max(drift)), 1) if drift.size else None,
        "jumps": jumps,
    }


def sample_frames_by_time(
    video: Path, start_s: float, end_s: float, step_s: float
) -> Iterator[tuple[float, Frame]]:
    """Frames every `step_s` seconds by seeking (fast; approximate frame positions are
    fine for stability and quality checks, not for labels)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise OSError(f"could not open video {video}")
    try:
        for t in np.arange(start_s, end_s, step_s):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, image = cap.read()
            if not ok:
                break
            yield float(t), cast(Frame, image)
    finally:
        cap.release()
