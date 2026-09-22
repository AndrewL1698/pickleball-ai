import pytest
from tests_support_camera import HEIGHT, WIDTH, synthetic_calibration

from pickleball_ml.court.camera import (
    DriftSample,
    camera_placement,
    court_visibility,
    focal_from_homography,
    summarize_drift,
)


@pytest.mark.parametrize("position", [(0.0, -40.0, 8.0), (6.0, -35.0, 12.0), (-4.0, -30.0, 5.0)])
def test_recovers_focal_and_camera_position(position: tuple[float, float, float]) -> None:
    calibration = synthetic_calibration(position, focal=1400.0)
    focal = focal_from_homography(calibration.homography, WIDTH, HEIGHT)
    assert focal == pytest.approx(1400.0, rel=0.01)
    placement = camera_placement(calibration, WIDTH, HEIGHT)
    assert placement.focal_source == "homography"
    assert placement.lateral_offset_ft == pytest.approx(position[0], abs=0.1)
    assert placement.behind_near_baseline_ft == pytest.approx(-22.0 - position[1], abs=0.1)
    assert placement.height_ft == pytest.approx(position[2], abs=0.1)
    assert 0 < placement.pitch_down_deg < 90


def test_visibility_reports_corners_and_foreshortening() -> None:
    visibility = court_visibility(synthetic_calibration((0.0, -40.0, 8.0), 1400.0),
                                  WIDTH, HEIGHT)
    assert all(visibility["corners_in_frame"].values())
    assert 0 < visibility["far_to_near_scale"] < 1


def test_drift_summary_lists_jumps() -> None:
    samples = [DriftSample(0, 0.0, None), DriftSample(10, 1.0, 1.0), DriftSample(20, 60.0, 59.0),
               DriftSample(30, None, None)]
    summary = summarize_drift(samples)
    assert summary["jumps"] == [{"time_s": 20, "step_px": 59.0}]
    assert summary["unmatched_samples"] == 1
    assert summary["max_drift_px"] == 60.0
