import pandas as pd
import pytest

from pickleball_ml.evaluation.players import (
    SCHEMA,
    PlayerLabel,
    best_mapping,
    evaluate_players,
    id_switches,
    parse_player_labels,
)


def box(x: float) -> list[float]:
    return [x, 0.0, x + 10.0, 20.0]


def predicted(frame: int, player_id: int, x: float) -> dict[str, float]:
    x1, y1, x2, y2 = box(x)
    return {"frame_number": frame, "player_id": player_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def labels_file(frames: list[dict[str, object]]) -> dict[str, object]:
    return {"schema": SCHEMA, "frames": frames}


def four(frame: int, split: str = "test") -> dict[str, object]:
    return {"frame_number": frame, "split": split, "players": [
        {"person": name, "box": box(x)} for name, x in
        (("a", 0), ("b", 100), ("c", 200), ("d", 300))
    ]}


def test_candidate_references_resolve_to_boxes() -> None:
    labels, frames = parse_player_labels(labels_file([{
        "frame_number": 5, "split": "tune",
        "candidates": [{"id": 0, "box": box(40)}],
        "players": [{"person": "a", "candidate": 0}, {"person": "b", "box": None}],
    }]))
    assert frames == {5: "tune"}
    assert labels == [PlayerLabel(5, "tune", "a", (40.0, 0.0, 50.0, 20.0)),
                      PlayerLabel(5, "tune", "b", None)]


def test_rejects_wrong_schema_and_duplicates() -> None:
    with pytest.raises(ValueError, match="schema"):
        parse_player_labels({"schema": "other", "frames": []})
    with pytest.raises(ValueError, match="twice"):
        parse_player_labels(labels_file([four(1), four(1)]))


def test_perfect_tracking() -> None:
    labels, frames = parse_player_labels(labels_file([four(0), four(10)]))
    players = pd.DataFrame([predicted(f, pid, x) for f in (0, 10)
                            for pid, x in ((1, 0), (2, 100), (3, 200), (4, 300))])
    report = evaluate_players(players, labels, frames)["all"]
    assert report["coverage"] == 1.0
    assert report["identity_accuracy_when_detected"] == 1.0
    assert report["frames_all_4_correct"] == 2
    assert report["id_switches"] == 0
    assert report["extra_predictions"] == 0


def test_miss_swap_extra_and_switch_are_counted() -> None:
    labels, frames = parse_player_labels(labels_file([four(0), four(10, "tune"), four(20)]))
    rows = [predicted(0, pid, x) for pid, x in ((1, 0), (2, 100), (3, 200), (4, 300))]
    # Frame 10: player "d" missed, a spectator predicted as player 4.
    rows += [predicted(10, pid, x) for pid, x in ((1, 0), (2, 100), (3, 200), (4, 900))]
    # Frame 20: players 1 and 2 swapped identities.
    rows += [predicted(20, pid, x) for pid, x in ((2, 0), (1, 100), (3, 200), (4, 300))]
    report = evaluate_players(pd.DataFrame(rows), labels, frames)
    everything = report["all"]
    assert everything["visible_player_labels"] == 12
    assert everything["missed"] == 1
    assert everything["wrong_identity"] == 2
    assert everything["extra_predictions"] == 1
    assert everything["frames_all_4_detected"] == 2
    assert everything["frames_all_4_correct"] == 1
    assert everything["id_switches"] == 2  # a: 1 -> 2, b: 2 -> 1
    assert report["tune"]["labeled_frames"] == 1
    assert report["test"]["labeled_frames"] == 2
    assert report["mapping"] == {"1": "a", "2": "b", "3": "c", "4": "d"}


def test_frame_with_no_visible_players_keeps_its_split() -> None:
    labels, frames = parse_player_labels(labels_file(
        [{"frame_number": 3, "split": "test", "players": []}]))
    report = evaluate_players(pd.DataFrame([predicted(3, 1, 0)]), labels, frames)
    assert report["test"]["labeled_frames"] == 1
    assert report["test"]["extra_predictions"] == 1


def test_id_switches_ignore_missed_frames() -> None:
    labels = [PlayerLabel(f, "t", "a", None) for f in (0, 1, 2, 3)]
    assert id_switches(labels, [1, None, 1, 2]) == (1, 2)


def test_best_mapping_is_one_to_one() -> None:
    pairs = [(1, "a")] * 3 + [(2, "a")] * 2 + [(2, "b")]
    assert best_mapping(pairs) == {1: "a", 2: "b"}
