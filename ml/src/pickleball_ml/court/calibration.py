"""Court calibration record: landmark clicks, fitted homography, quality, and overlay."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pickleball_ml.court.geometry import (
    FloatArray,
    apply_homography,
    fit_homography,
    reprojection_errors,
)
from pickleball_ml.court.spec import LANDMARKS, court_lines
from pickleball_ml.video.reader import Frame


@dataclass(frozen=True)
class Calibration:
    video_path: str
    frame_number: int
    source: str  # "manual" or "model"
    image_points: dict[str, tuple[float, float]]
    homography: FloatArray  # image pixels -> court feet
    reprojection_error_ft: dict[str, float]
    created_at: str
    reference_image: str = "frame"  # "frame", or how a composite image was built

    @classmethod
    def fit(
        cls,
        video_path: str,
        frame_number: int,
        image_points: dict[str, tuple[float, float]],
        source: str = "manual",
        reference_image: str = "frame",
    ) -> "Calibration":
        unknown = set(image_points) - set(LANDMARKS)
        if unknown:
            raise ValueError(f"unknown landmark names: {sorted(unknown)}")
        names = [name for name in LANDMARKS if name in image_points]
        img = np.array([image_points[n] for n in names], dtype=np.float64)
        court = np.array([LANDMARKS[n] for n in names], dtype=np.float64)
        homography = fit_homography(img, court)
        errors = reprojection_errors(homography, img, court)
        return cls(
            video_path=video_path,
            frame_number=frame_number,
            source=source,
            image_points={n: image_points[n] for n in names},
            homography=homography,
            reprojection_error_ft={n: float(e) for n, e in zip(names, errors, strict=True)},
            created_at=datetime.now(UTC).isoformat(),
            reference_image=reference_image,
        )

    @property
    def mean_error_ft(self) -> float:
        return float(np.mean(list(self.reprojection_error_ft.values())))

    @property
    def max_error_ft(self) -> float:
        return float(np.max(list(self.reprojection_error_ft.values())))

    def image_to_court(self, points: FloatArray) -> FloatArray:
        return apply_homography(self.homography, points)

    def court_to_image(self, points: FloatArray) -> FloatArray:
        return apply_homography(np.linalg.inv(self.homography), points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "frame_number": self.frame_number,
            "reference_image": self.reference_image,
            "source": self.source,
            "image_points": {n: list(p) for n, p in self.image_points.items()},
            "court_points": {n: list(LANDMARKS[n]) for n in self.image_points},
            "homography": self.homography.tolist(),
            "reprojection_error_ft": self.reprojection_error_ft,
            "mean_reprojection_error_ft": self.mean_error_ft,
            "max_reprojection_error_ft": self.max_error_ft,
            "created_at": self.created_at,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        data = json.loads(path.read_text())
        return cls(
            video_path=data["video_path"],
            frame_number=data["frame_number"],
            source=data["source"],
            image_points={n: (p[0], p[1]) for n, p in data["image_points"].items()},
            homography=np.array(data["homography"], dtype=np.float64),
            reprojection_error_ft=data["reprojection_error_ft"],
            created_at=data["created_at"],
            reference_image=data.get("reference_image", "frame"),
        )


def draw_court_overlay(
    image: Frame,
    calibration: Calibration,
    color: tuple[int, int, int] = (0, 0, 255),
    thickness: int = 2,
    draw_landmarks: bool = True,
) -> Frame:
    """Draw court lines projected through the calibration onto a copy of the image.

    Lines are sampled densely so the overlay reveals lens distortion (painted
    lines that curve away from the straight projected lines).
    """
    out = image.copy()
    draw_court_lines(out, calibration, color, thickness)
    if draw_landmarks:
        for name, (x, y) in calibration.image_points.items():
            center = (round(x), round(y))
            cv2.circle(out, center, 6, (0, 255, 255), 2)
            label = f"{name} {calibration.reprojection_error_ft[name]:.2f}ft"
            cv2.putText(out, label, (center[0] + 8, center[1] - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return out


def draw_court_lines(
    image: Frame, calibration: Calibration, color: tuple[int, int, int], thickness: int
) -> None:
    """Draw projected court lines in place."""
    inverse = np.linalg.inv(calibration.homography)
    for (x0, y0), (x1, y1) in court_lines().values():
        t = np.linspace(0.0, 1.0, 40)[:, None]
        samples = np.hstack([x0 + t * (x1 - x0), y0 + t * (y1 - y0)])
        projected = apply_homography(inverse, samples)
        finite = projected[np.all(np.isfinite(projected), axis=1)]
        if len(finite) >= 2:
            cv2.polylines(image, [np.round(finite).astype(np.int32)], False, color, thickness,
                          cv2.LINE_AA)
