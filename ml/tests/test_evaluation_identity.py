import pandas as pd

from pickleball_ml.evaluation.identity import box_iou, evaluate_identity


def player(frame: int, player_id: int, x: float) -> dict[str, float]:
    return {"frame_number": frame, "player_id": player_id, "x1": x, "y1": 0.0, "x2": x + 10,
            "y2": 20.0}


def label(frame: int, x: float, person: str) -> dict[str, object]:
    return {"frame_number": frame, "box": [x, 0.0, x + 10, 20.0], "person": person,
            "split": "test"}


def test_box_iou() -> None:
    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert box_iou((0, 0, 10, 10), (5, 0, 15, 10)) == 1 / 3
    assert box_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_swap_missed_and_correct_are_counted_with_best_mapping() -> None:
    players = pd.DataFrame([
        player(0, 1, 0), player(0, 2, 50),
        player(1, 1, 0), player(1, 2, 50),
        player(2, 2, 0), player(2, 1, 50),  # identities swapped in frame 2
    ])
    samples = [
        label(0, 0, "alice"), label(0, 50, "bob"),
        label(1, 0, "alice"), label(1, 50, "bob"),
        label(2, 0, "alice"), label(2, 50, "bob"),
        label(3, 0, "alice"),  # no prediction in frame 3
    ]
    result = evaluate_identity(players, samples)
    assert result["mapping"] == {"1": "alice", "2": "bob"}
    assert (result["correct"], result["wrong_identity"], result["missed"]) == (4, 2, 1)
