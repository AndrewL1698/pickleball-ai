"""Fitting and reviewing calibrations, without HTTP."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pickleball_api.calibration import (
    REVIEW_MAX_ERROR_FT,
    REVIEW_MEAN_ERROR_FT,
    CalibrationRejected,
    CourtLandmark,
    calibration_warnings,
    fit_calibration,
)
from pickleball_api.testing import REPO_ROOT
from pickleball_ml.court.spec import LANDMARKS

ANNOTATED = sorted((REPO_ROOT / "data" / "annotations").glob("*/calibration_points*.json"))
#: Phase 0 clips, whatever their exact resolution: nothing clicked was past this.
ANY_FRAME = {"width": 3840, "height": 2160}

BOTH_HALVES = {"near_left_kitchen": (-10.0, -7.0), "far_left_kitchen": (-10.0, 7.0)}
FIVE = {
    **BOTH_HALVES,
    "near_right_kitchen": (10.0, -7.0),
    "far_right_kitchen": (10.0, 7.0),
    "near_center_kitchen": (0.0, -7.0),
}


def points_from(path: Path) -> dict[str, tuple[float, float]]:
    return {name: (float(x), float(y)) for name, (x, y) in json.loads(path.read_text()).items()}


def test_the_landmarks_the_api_names_are_the_ones_the_court_has() -> None:
    assert {landmark.value for landmark in CourtLandmark} == set(LANDMARKS)


def test_there_are_real_calibrations_to_check_against() -> None:
    assert len(ANNOTATED) >= 6


@pytest.mark.parametrize("path", ANNOTATED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_every_real_calibration_passes_and_needs_no_review(path: Path) -> None:
    """The cameras Phase 0 measured, corner camera included. None is mirrored, and
    the review thresholds do not cry wolf on a careful calibration."""
    fitted = fit_calibration(points_from(path), **ANY_FRAME)
    assert fitted.mean_reprojection_error_ft < REVIEW_MEAN_ERROR_FT
    assert fitted.max_reprojection_error_ft < REVIEW_MAX_ERROR_FT
    court = {name: (x, y) for name, (x, y) in fitted.court_points.items()}
    assert (
        calibration_warnings(
            court, fitted.mean_reprojection_error_ft, fitted.max_reprojection_error_ft
        )
        == []
    )


@pytest.mark.parametrize("path", ANNOTATED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_every_real_calibration_is_refused_with_left_and_right_swapped(path: Path) -> None:
    swapped = {
        name.replace("left", "@").replace("right", "left").replace("@", "right"): point
        for name, point in points_from(path).items()
    }
    with pytest.raises(CalibrationRejected, match="swapped"):
        fit_calibration(swapped, **ANY_FRAME)


def test_the_request_order_does_not_change_the_result() -> None:
    points = points_from(ANNOTATED[0])
    reversed_points = dict(reversed(list(points.items())))
    assert fit_calibration(points, **ANY_FRAME) == fit_calibration(reversed_points, **ANY_FRAME)


def test_a_good_spread_with_a_small_error_has_no_warnings() -> None:
    assert calibration_warnings(FIVE, 0.2, 0.4) == []


def test_four_points_warn_that_the_error_means_nothing() -> None:
    four = dict(list(FIVE.items())[:4])
    assert any("exactly" in warning for warning in calibration_warnings(four, 0.0, 0.0))


@pytest.mark.parametrize(
    ("mean", "largest"),
    [(REVIEW_MEAN_ERROR_FT + 0.01, 0.6), (0.2, REVIEW_MAX_ERROR_FT + 0.01)],
)
def test_a_large_error_asks_for_a_review(mean: float, largest: float) -> None:
    assert any("disagree" in warning for warning in calibration_warnings(FIVE, mean, largest))


def test_landmarks_on_one_half_warn_about_the_other() -> None:
    near = {name: point for name, point in LANDMARKS.items() if name.startswith("near")}
    assert any("extrapolated" in warning for warning in calibration_warnings(near, 0.1, 0.2))


def test_the_api_does_not_import_torch_or_ultralytics() -> None:
    """Court geometry only: no model libraries in the API process."""
    code = (
        "import sys, pickleball_api.main;"
        "heavy = [m for m in ('torch', 'ultralytics') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert output == ""
