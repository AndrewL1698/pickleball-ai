import numpy as np
import pandas as pd
import pytest

from pickleball_ml.evaluation.ground_contact import (
    SCHEMA,
    GroundContactFrame,
    evaluate_ground_contact,
    parse_ground_contact_labels,
)

# 10 image px per court foot; image y = 440 is the near baseline (court y = -22) and
# y = 0 the far baseline, so court y = 22 - y / 10 and court x = x / 10 - 10.
HOMOGRAPHY = np.array([[0.1, 0.0, -10.0], [0.0, -0.1, 22.0], [0.0, 0.0, 1.0]])


def players(rows: list[tuple[int, float, float, float, bool]]) -> pd.DataFrame:
    """(frame, ground-contact x, ground-contact y, box height, truncated) per player."""
    return pd.DataFrame([
        {"frame_number": f, "player_id": i + 1, "x1": x - 10, "y1": y - height,
         "x2": x + 10, "y2": y, "image_x": x, "image_y": y, "truncated": truncated}
        for i, (f, x, y, height, truncated) in enumerate(rows)
    ])


def labels(*frames: dict[str, object]) -> list[GroundContactFrame]:
    return parse_ground_contact_labels({"schema": SCHEMA, "frames": list(frames)})


def frame(number: int, *points: list[float], window: str = "w", split: str = "test",
          unsure: tuple[int, ...] = ()) -> dict[str, object]:
    return {"frame_number": number, "window": window, "split": split,
            "points": [{"xy": xy, **({"unsure": True} if i in unsure else {})}
                       for i, xy in enumerate(points)]}


def test_rejects_malformed_label_files() -> None:
    with pytest.raises(ValueError, match="expected schema"):
        parse_ground_contact_labels({"schema": "pbml.ball_labels/v1", "frames": []})
    with pytest.raises(ValueError, match="labeled twice"):
        parse_ground_contact_labels({"schema": SCHEMA,
                                     "frames": [frame(1, [0, 0]), frame(1, [5, 5])]})
    with pytest.raises(ValueError, match="needs an xy"):
        parse_ground_contact_labels({"schema": SCHEMA, "frames": [
            {"frame_number": 1, "points": [{"unsure": True}]}]})


def test_skipped_frames_are_not_labels() -> None:
    labels = parse_ground_contact_labels({"schema": SCHEMA, "frames": [
        frame(10, [1.0, 2.0], [3.0, 4.0], unsure=(1,)),
        {"frame_number": 20, "window": "w", "split": "test", "skip": True},
    ]})
    assert [label.frame_number for label in labels] == [10]
    assert labels[0].window == "w"
    assert [(p.xy, p.unsure) for p in labels[0].points] == [((1.0, 2.0), False),
                                                            ((3.0, 4.0), True)]


def test_matched_point_error_in_pixels_and_court_feet() -> None:
    # Label at court (0, -8); prediction 30 px right and 40 px lower: (3, -12).
    report = evaluate_ground_contact(players([(5, 130.0, 340.0, 200.0, False)]),
                                     labels(frame(5, [100.0, 300.0])), HOMOGRAPHY)
    everything = report["all"]
    assert everything["labeled_points"] == 1
    assert everything["matched"] == 1
    assert everything["median_error_px"] == 50.0
    assert everything["median_error_ft"] == 5.0
    # 4 ft nearer the camera than the label
    assert everything["median_depth_error_ft"] == -4.0


def test_matching_is_one_to_one_and_gated_by_box_height() -> None:
    # Both labels are nearest the prediction at x=104. The first takes it; the
    # prediction at x=200 is 90 px from the second label, more than half its 100 px
    # box, so the second label is missed and that prediction is an extra.
    report = evaluate_ground_contact(
        players([(5, 104.0, 300.0, 100.0, False), (5, 200.0, 300.0, 100.0, False)]),
        labels(frame(5, [100.0, 300.0], [110.0, 300.0])), HOMOGRAPHY,
    )
    everything = report["all"]
    assert everything["matched"] == 1
    assert everything["missed"] == 1
    assert everything["coverage"] == 0.5
    assert everything["median_error_px"] == 4.0
    assert report["extra_predictions"] == 1


def test_a_labeled_frame_without_predictions_misses_every_point() -> None:
    report = evaluate_ground_contact(players([(6, 100.0, 300.0, 100.0, False)]),
                                     labels(frame(5, [100.0, 300.0])), HOMOGRAPHY)
    assert report["all"]["missed"] == 1
    assert report["all"]["median_error_px"] is None
    assert report["extra_predictions"] == 0


def test_breakdown_by_side_and_truncation() -> None:
    report = evaluate_ground_contact(
        players([
            (5, 100.0, 310.0, 100.0, False),  # near label: 10 px, 1 ft
            (5, 150.0, 430.0, 100.0, True),   # near label, box cut by the frame: 30 px, 3 ft
            (5, 100.0, 120.0, 40.0, False),   # far label: 20 px, 2 ft
        ]),
        labels(frame(5, [100.0, 300.0], [150.0, 400.0], [100.0, 100.0],
                     [180.0, 50.0])),  # the last (far) label has no prediction
        HOMOGRAPHY,
    )
    near, far = report["near"], report["far"]
    assert (near["labeled_points"], near["matched"], near["missed"]) == (2, 2, 0)
    assert near["median_error_px"] == 20.0
    assert near["p90_error_ft"] == 2.8
    assert (far["labeled_points"], far["matched"], far["missed"]) == (2, 1, 1)
    assert far["median_error_ft"] == 2.0
    # Truncation is a property of the prediction, so only matched points are split by it.
    assert report["truncated"]["matched"] == 1
    assert report["truncated"]["median_error_px"] == 30.0
    assert report["not_truncated"]["matched"] == 2
    assert report["not_truncated"]["median_error_px"] == 15.0


def test_unsure_points_absorb_a_prediction_but_are_not_scored() -> None:
    report = evaluate_ground_contact(
        players([(5, 100.0, 330.0, 100.0, True), (5, 200.0, 310.0, 100.0, False)]),
        labels(frame(5, [100.0, 300.0], [200.0, 300.0], unsure=(0,))), HOMOGRAPHY,
    )
    assert report["extra_predictions"] == 0
    assert report["all"]["unsure_points"] == 1
    assert report["all"]["labeled_points"] == 1
    assert report["all"]["median_error_px"] == 10.0
    assert report["truncated"]["matched"] == 0
