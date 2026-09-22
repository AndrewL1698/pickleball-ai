import numpy as np
import pandas as pd
import pytest

from pickleball_ml.evaluation.ball import SCHEMA, evaluate_ball, parse_ball_labels


def track(rows: list[tuple[int, float, float, bool, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"frame_number": f, "timestamp_ms": 0.0, "x": x, "y": y, "score": 0.5,
          "visible": visible, "candidate": 0, "interpolated": interpolated,
          "speed_px_s": np.nan}
         for f, x, y, visible, interpolated in rows]
    )


def labels(frames: list[dict[str, object]]) -> list:
    return parse_ball_labels({"schema": SCHEMA, "frames": frames})


def visible(frame: int, x: float, y: float, split: str = "test") -> dict[str, object]:
    return {"frame_number": frame, "split": split, "state": "visible", "xy": [x, y]}


def test_rejects_visible_label_without_a_position() -> None:
    with pytest.raises(ValueError, match="needs an xy"):
        labels([{"frame_number": 1, "state": "visible"}])
    with pytest.raises(ValueError, match="unknown state"):
        labels([{"frame_number": 1, "state": "maybe"}])


def test_recall_precision_and_error_at_tolerances() -> None:
    predicted = track([
        (0, 100.0, 100.0, True, False),    # 4 px from the label
        (1, 100.0, 100.0, True, False),    # 30 px from the label
        (2, np.nan, np.nan, False, False),  # nothing reported
        (3, 500.0, 500.0, True, False),    # reported although the ball is absent
    ])
    report = evaluate_ball(predicted, labels([
        visible(0, 104.0, 100.0), visible(1, 130.0, 100.0), visible(2, 200.0, 200.0),
        {"frame_number": 3, "split": "test", "state": "absent"},
    ]), tolerances_px=(5.0, 40.0))
    everything = report["all"]
    assert everything["visible_labels"] == 3
    assert everything["reported_on_visible"] == 2
    assert everything["coverage"] == round(2 / 3, 4)
    assert everything["reported_on_absent"] == 1
    assert everything["median_error_px"] == 17.0
    assert everything["at_tolerance"]["5px"] == {"hits": 1, "recall": round(1 / 3, 4),
                                                 "precision": round(1 / 3, 4)}
    assert everything["at_tolerance"]["40px"]["hits"] == 2


def test_unsure_frames_are_excluded() -> None:
    report = evaluate_ball(track([(0, 100.0, 100.0, True, False)]), labels([
        {"frame_number": 0, "split": "test", "state": "unsure"},
    ]))
    assert report["all"]["visible_labels"] == 0
    assert report["all"]["unsure_labels"] == 1
    assert report["all"]["coverage"] is None


def test_interpolated_positions_are_not_counted_as_observations() -> None:
    predicted = track([(0, 100.0, 100.0, False, True)])
    labelled = labels([visible(0, 100.0, 100.0)])
    assert evaluate_ball(predicted, labelled)["all"]["reported_on_visible"] == 0
    counted = evaluate_ball(predicted, labelled, count_interpolated=True)
    assert counted["all"]["reported_on_visible"] == 0  # visible flag is still false


def test_splits_are_reported_separately() -> None:
    predicted = track([(0, 100.0, 100.0, True, False), (1, 300.0, 300.0, True, False)])
    report = evaluate_ball(predicted, labels([
        visible(0, 100.0, 100.0, "tune"), visible(1, 500.0, 500.0, "test"),
    ]), tolerances_px=(10.0,))
    assert report["tune"]["at_tolerance"]["10px"]["recall"] == 1.0
    assert report["test"]["at_tolerance"]["10px"]["recall"] == 0.0


def test_labels_outside_the_processed_frames_are_reported() -> None:
    report = evaluate_ball(track([(0, 1.0, 1.0, True, False)]),
                           labels([visible(0, 1.0, 1.0), visible(99, 5.0, 5.0)]))
    assert report["labels_outside_track"] == 1
    assert report["all"]["coverage"] == 0.5
