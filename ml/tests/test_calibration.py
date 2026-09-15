from pathlib import Path

import numpy as np
import pytest

from pickleball_ml.court.calibration import Calibration, draw_court_overlay
from pickleball_ml.court.geometry import apply_homography
from pickleball_ml.court.spec import LANDMARKS

COURT_TO_IMAGE = np.array([[42.0, 8.0, 960.0], [0.0, -9.0, 700.0], [0.0, 0.012, 1.0]])


def clicks(names: list[str]) -> dict[str, tuple[float, float]]:
    img = apply_homography(COURT_TO_IMAGE, np.array([LANDMARKS[n] for n in names]))
    return {n: (float(x), float(y)) for n, (x, y) in zip(names, img, strict=True)}


def test_fit_skips_missing_landmarks_and_reports_errors() -> None:
    names = [n for n in LANDMARKS if n != "far_left_kitchen"]
    cal = Calibration.fit("video.mp4", 10, clicks(names))
    assert list(cal.image_points) == names
    assert set(cal.reprojection_error_ft) == set(names)
    assert cal.max_error_ft < 1e-4


def test_fit_rejects_unknown_landmark() -> None:
    points = clicks(list(LANDMARKS)[:4]) | {"net_post": (1.0, 2.0)}
    with pytest.raises(ValueError, match="net_post"):
        Calibration.fit("video.mp4", 0, points)


def test_save_load_round_trip(tmp_path: Path) -> None:
    cal = Calibration.fit("video.mp4", 42, clicks(list(LANDMARKS)), reference_image="median")
    path = tmp_path / "calibration.json"
    cal.save(path)
    loaded = Calibration.load(path)
    assert loaded.frame_number == 42
    assert loaded.reference_image == "median"
    assert loaded.image_points == cal.image_points
    np.testing.assert_allclose(loaded.homography, cal.homography)


def test_court_to_image_inverts_image_to_court() -> None:
    cal = Calibration.fit("video.mp4", 0, clicks(list(LANDMARKS)))
    feet = np.array([[2.0, -15.0], [-8.0, 20.0]])
    np.testing.assert_allclose(cal.image_to_court(cal.court_to_image(feet)), feet, atol=1e-4)


def test_overlay_draws_on_a_copy() -> None:
    cal = Calibration.fit("video.mp4", 0, clicks(list(LANDMARKS)))
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    out = draw_court_overlay(image, cal)
    assert image.sum() == 0
    assert out.sum() > 0
