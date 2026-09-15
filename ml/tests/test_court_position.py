import numpy as np
import pandas as pd

from pickleball_ml.players.court_position import PlayArea, foot_points, project_detections


def test_foot_point_is_bottom_center() -> None:
    np.testing.assert_allclose(foot_points(np.array([[10, 20, 30, 80]])), [[20, 80]])


def test_play_area_uses_separate_end_margins() -> None:
    area = PlayArea(side_margin_ft=5, near_end_margin_ft=10, far_end_margin_ft=4)
    x = np.array([0, 0, 0, 0, 15.5, -15])
    y = np.array([-31, -33, 25.5, 26.5, 0, 0])
    assert area.contains(x, y).tolist() == [True, False, True, False, False, True]


def test_projection_and_truncation_flag() -> None:
    df = pd.DataFrame({"x1": [3.0, 0.0], "y1": [0.0, 0.0], "x2": [5.0, 2.0], "y2": [-12.0, 1079.0]})
    out = project_detections(df, np.eye(3), frame_height=1080)
    assert out.loc[0, ["court_x", "court_y"]].tolist() == [4.0, -12.0]
    assert out["truncated"].tolist() == [False, True]
