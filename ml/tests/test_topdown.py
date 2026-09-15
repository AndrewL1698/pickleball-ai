from pickleball_ml.render.topdown import CourtCanvas


def test_canvas_orients_near_baseline_at_bottom() -> None:
    canvas = CourtCanvas(height_px=640, side_view_ft=8, end_view_ft=10)
    assert canvas.px_per_ft == 10
    assert canvas.width_px == 360
    assert canvas.to_px(-18, 32) == (0, 0)
    assert canvas.to_px(18, -32) == (360, 640)
    assert canvas.to_px(0, 0) == (180, 320)
    _, near_y = canvas.to_px(0, -22)
    _, far_y = canvas.to_px(0, 22)
    assert near_y > far_y


def test_blank_canvas_has_expected_shape() -> None:
    canvas = CourtCanvas(height_px=320)
    assert canvas.blank().shape == (320, canvas.width_px, 3)
