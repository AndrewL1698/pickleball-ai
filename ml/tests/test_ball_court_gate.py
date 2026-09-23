import numpy as np
from tests_support_camera import synthetic_calibration

from pickleball_ml.ball.court_gate import BallGate, gate_polygon, inside
from pickleball_ml.court.spec import HALF_LENGTH_FT, HALF_WIDTH_FT


def test_gate_covers_the_court_and_the_air_above_it() -> None:
    calibration = synthetic_calibration((0.0, -40.0, 8.0), focal=1400.0)
    polygon = gate_polygon(calibration, BallGate())
    court = calibration.court_to_image(
        np.array([[-HALF_WIDTH_FT, -HALF_LENGTH_FT], [HALF_WIDTH_FT, -HALF_LENGTH_FT],
                  [HALF_WIDTH_FT, HALF_LENGTH_FT], [-HALF_WIDTH_FT, HALF_LENGTH_FT],
                  [0.0, 0.0]])
    )
    assert inside(polygon, court).all()
    # A ball in the air above the far court is inside; the stands well above are not.
    far_middle = court[2:4].mean(axis=0)
    court_height = float(court[:2, 1].mean() - court[2:4, 1].mean())
    assert inside(polygon, np.array([[far_middle[0], far_middle[1] - 0.5 * court_height]]))[0]
    assert not inside(polygon, np.array([[far_middle[0], far_middle[1] - 2 * court_height]]))[0]


def test_a_ball_on_the_neighbouring_court_is_outside() -> None:
    calibration = synthetic_calibration((0.0, -40.0, 8.0), focal=1400.0)
    polygon = gate_polygon(calibration, BallGate())
    # 30 ft to the side is the next court over, well beyond the 6 ft margin.
    neighbour = calibration.court_to_image(np.array([[HALF_WIDTH_FT + 30.0, 0.0]]))
    assert not inside(polygon, neighbour)[0]


def test_non_finite_points_are_outside() -> None:
    calibration = synthetic_calibration((0.0, -40.0, 8.0), focal=1400.0)
    polygon = gate_polygon(calibration, BallGate())
    assert not inside(polygon, np.array([[np.nan, np.nan]]))[0]
