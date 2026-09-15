from pickleball_ml.court.spec import (
    HALF_LENGTH_FT,
    HALF_WIDTH_FT,
    KITCHEN_DEPTH_FT,
    LANDMARKS,
    court_lines,
)


def test_court_dimensions_match_standard_court() -> None:
    assert 2 * HALF_WIDTH_FT == 20
    assert 2 * HALF_LENGTH_FT == 44
    assert KITCHEN_DEPTH_FT == 7


def test_landmarks_are_symmetric_about_the_net() -> None:
    assert len(LANDMARKS) == 12
    for name, (x, y) in LANDMARKS.items():
        mirrored = name.replace("near_", "far_") if name.startswith("near_") else name.replace(
            "far_", "near_"
        )
        assert LANDMARKS[mirrored] == (x, -y)


def test_near_half_is_negative_y_and_right_is_positive_x() -> None:
    assert LANDMARKS["near_right_baseline_corner"] == (10.0, -22.0)
    assert LANDMARKS["far_left_kitchen"] == (-10.0, 7.0)


def test_center_lines_stop_at_kitchen_lines() -> None:
    lines = court_lines()
    assert lines["near_center_line"] == ((0.0, -22.0), (0.0, -7.0))
    assert lines["far_center_line"] == ((0.0, 7.0), (0.0, 22.0))
