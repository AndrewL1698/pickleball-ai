import numpy as np
import pandas as pd

from pickleball_ml.ball.detector import processed_frames, stride_for_target_fps
from pickleball_ml.ball.track import (
    BallTrackConfig,
    interpolate_short_gaps,
    run_record,
    select_track,
)

FPS = 30.0
CONFIG = BallTrackConfig()


def candidates(rows: list[tuple[int, int, float, float, float]]) -> pd.DataFrame:
    """(frame, candidate index, x, y, score); candidate -1 means the frame found nothing."""
    return pd.DataFrame(
        [{"frame_number": f, "timestamp_ms": f / FPS * 1000, "candidate": c,
          "x": x, "y": y, "score": s} for f, c, x, y, s in rows],
        columns=["frame_number", "timestamp_ms", "candidate", "x", "y", "score"],
    )


def track_of(frame: pd.DataFrame, config: BallTrackConfig = CONFIG) -> pd.DataFrame:
    frames, timestamps = processed_frames(frame)
    return select_track(frame, frames, timestamps, FPS, config)


def test_follows_a_moving_ball() -> None:
    rows = [(f, 0, 100.0 + 8 * f, 200.0, 0.8) for f in range(10)]
    track = track_of(candidates(rows))
    assert track["visible"].all()
    assert track["x"].tolist() == [100.0 + 8 * f for f in range(10)]
    assert track["interpolated"].any() == False  # noqa: E712 - explicit about the flag


def test_stays_on_an_established_trajectory_against_a_brief_stronger_blob() -> None:
    # A ball crossing the frame at a constant 120 px per frame. In frame 4 a brighter blob
    # appears 140 px off the trajectory, closer to the previous position than the ball is.
    rows: list[tuple[int, int, float, float, float]] = []
    for f in range(8):
        rows.append((f, 0, 100.0 + 120 * f, 200.0, 0.55))
    rows.append((4, 1, 100.0 + 120 * 3 + 20, 200.0, 0.75))
    track = track_of(candidates(rows))
    assert track["candidate"].tolist() == [0] * 8
    assert track["x"].tolist() == [100.0 + 120 * f for f in range(8)]


def test_a_stationary_distractor_still_wins_when_only_scores_separate_them() -> None:
    # Documents the limit of post-processing: with no trajectory established and a
    # higher-scoring stationary blob, the filter cannot know which is the ball.
    rows: list[tuple[int, int, float, float, float]] = []
    for f in range(6):
        rows.append((f, 0, 100.0 + 5 * f, 200.0, 0.60))
        rows.append((f, 1, 1700.0, 900.0, 0.62))
    assert track_of(candidates(rows))["candidate"].tolist() == [1] * 6


def test_a_physically_impossible_jump_is_never_taken() -> None:
    config = BallTrackConfig(max_speed_px_s=1000.0)
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, 0, 1800.0, 900.0, 0.9), (2, 0, 110.0, 100.0, 0.9)]
    track = track_of(candidates(rows), config)
    # The middle frame cannot be reached from either neighbour at 1000 px/s.
    assert track["visible"].tolist() == [True, False, True]


def test_weak_isolated_candidate_is_dropped_and_the_gap_is_kept() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, 0, 400.0, 400.0, 0.06), (2, 0, 100.0, 100.0, 0.9)]
    track = track_of(candidates(rows))
    assert track["visible"].tolist() == [True, False, True]
    assert bool(np.isnan(track.loc[1, "x"]))  # nothing is invented for the missing frame


def test_frames_with_no_candidates_stay_missing() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, -1, np.nan, np.nan, np.nan),
            (2, 0, 120.0, 100.0, 0.9)]
    track = track_of(candidates(rows))
    assert len(track) == 3
    assert track["visible"].tolist() == [True, False, True]
    assert track["frame_number"].tolist() == [0, 1, 2]


def test_speed_is_reported_between_visible_frames() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, 0, 130.0, 100.0, 0.9)]
    track = track_of(candidates(rows))
    assert track.loc[1, "speed_px_s"] == 30.0 * FPS


def test_interpolation_is_off_by_default_and_flagged_when_enabled() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, -1, np.nan, np.nan, np.nan),
            (2, 0, 200.0, 100.0, 0.9)]
    default = track_of(candidates(rows))
    assert bool(np.isnan(default.loc[1, "x"]))
    filled = track_of(candidates(rows), BallTrackConfig(interpolate_max_gap_frames=3))
    assert filled.loc[1, "x"] == 150.0
    assert bool(filled.loc[1, "interpolated"])
    assert not bool(filled.loc[1, "visible"])  # interpolated is not an observation


def test_interpolation_leaves_long_gaps_alone() -> None:
    track = pd.DataFrame({
        "frame_number": [0, 10, 20], "timestamp_ms": [0.0, 333.0, 666.0],
        "x": [100.0, np.nan, 200.0], "y": [1.0, np.nan, 2.0], "score": [0.9, np.nan, 0.9],
        "visible": [True, False, True], "candidate": [0, -1, 0],
        "interpolated": [False, False, False], "speed_px_s": [np.nan, np.nan, np.nan],
    })
    assert bool(np.isnan(interpolate_short_gaps(track, 5).loc[1, "x"]))


def test_run_record_summarizes_coverage_and_gaps() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (1, -1, np.nan, np.nan, np.nan),
            (2, -1, np.nan, np.nan, np.nan), (3, 0, 130.0, 100.0, 0.7)]
    frame = candidates(rows)
    record = run_record(CONFIG, {"model_name": "x"}, track_of(frame), frame)
    assert record["frames"] == 4
    assert record["frames_with_ball"] == 2
    assert record["coverage"] == 0.5
    assert record["longest_gap_frames"] == 2
    assert record["median_score"] == 0.8


def test_stride_keeps_apparent_motion_near_the_training_frame_rate() -> None:
    assert stride_for_target_fps(60.0, 30.0) == 2
    assert stride_for_target_fps(59.94, 30.0) == 2  # 29.97 fps effective
    assert stride_for_target_fps(120.0, 30.0) == 4
    assert stride_for_target_fps(30.0, 30.0) == 1


def test_candidates_outside_the_court_gate_are_not_selected() -> None:
    rows = [(0, 0, 100.0, 100.0, 0.9), (0, 1, 900.0, 900.0, 0.4),
            (1, 0, 120.0, 100.0, 0.9), (1, 1, 900.0, 900.0, 0.4)]
    frame = candidates(rows)
    frame["in_gate"] = [False, True, False, True]  # the strong candidates are off our court
    frames, timestamps = processed_frames(frame)
    track = select_track(frame, frames, timestamps, FPS, CONFIG)
    assert track["x"].tolist() == [900.0, 900.0]
