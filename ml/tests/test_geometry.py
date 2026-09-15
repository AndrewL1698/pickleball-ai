import numpy as np
import pytest

from pickleball_ml.court.geometry import (
    CalibrationError,
    apply_homography,
    fit_homography,
    reprojection_errors,
)
from pickleball_ml.court.spec import LANDMARKS

# A plausible perspective camera: court feet -> image pixels.
COURT_TO_IMAGE = np.array(
    [
        [42.0, 8.0, 960.0],
        [0.0, -9.0, 700.0],
        [0.0, 0.012, 1.0],
    ]
)


def court_points() -> np.ndarray:
    return np.array(list(LANDMARKS.values()))


def image_points() -> np.ndarray:
    return apply_homography(COURT_TO_IMAGE, court_points())


def test_apply_homography_identity_and_translation() -> None:
    pts = np.array([[1.0, 2.0], [-3.0, 4.5]])
    np.testing.assert_allclose(apply_homography(np.eye(3), pts), pts)
    translate = np.array([[1, 0, 5], [0, 1, -2], [0, 0, 1]], dtype=float)
    np.testing.assert_allclose(apply_homography(translate, pts), pts + [5, -2])


def test_apply_homography_point_at_infinity_is_nan() -> None:
    h = np.array([[1, 0, 0], [0, 1, 0], [1, 0, 0]], dtype=float)
    out = apply_homography(h, np.array([[0.0, 3.0]]))
    assert np.isnan(out).all()


def test_fit_recovers_exact_homography_from_all_landmarks() -> None:
    h = fit_homography(image_points(), court_points())
    np.testing.assert_allclose(apply_homography(h, image_points()), court_points(), atol=1e-4)
    assert reprojection_errors(h, image_points(), court_points()).max() < 1e-4


def test_fit_maps_unseen_points_correctly() -> None:
    h = fit_homography(image_points(), court_points())
    player_feet = np.array([[3.5, -18.0], [-6.0, 9.5], [0.0, 25.0]])
    np.testing.assert_allclose(
        apply_homography(h, apply_homography(COURT_TO_IMAGE, player_feet)), player_feet, atol=1e-4
    )


def test_fit_with_pixel_noise_stays_within_a_fraction_of_a_foot() -> None:
    rng = np.random.default_rng(0)
    noisy = image_points() + rng.normal(0, 1.0, size=image_points().shape)
    h = fit_homography(noisy, court_points())
    assert reprojection_errors(h, noisy, court_points()).mean() < 0.5


def test_fit_works_with_four_points() -> None:
    corners = [0, 2, 9, 11]
    h = fit_homography(image_points()[corners], court_points()[corners])
    np.testing.assert_allclose(apply_homography(h, image_points()), court_points(), atol=1e-4)


def test_fit_rejects_too_few_points() -> None:
    with pytest.raises(CalibrationError, match="at least 4"):
        fit_homography(image_points()[:3], court_points()[:3])


def test_fit_rejects_mismatched_lengths() -> None:
    with pytest.raises(CalibrationError, match="image points"):
        fit_homography(image_points()[:5], court_points()[:6])


def test_fit_rejects_collinear_points() -> None:
    baseline = [0, 1, 2]
    pts = court_points()[baseline]
    court = np.vstack([pts, [[5.0, -22.0]]])
    image = apply_homography(COURT_TO_IMAGE, court)
    with pytest.raises(CalibrationError, match="collinear"):
        fit_homography(image, court)
