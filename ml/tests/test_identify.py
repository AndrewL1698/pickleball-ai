"""Identify over Detect's rows: projection, chunk boundaries, partial input, numbering."""

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from pickleball_ml.players.appearance import DESCRIPTOR_SIZE
from pickleball_ml.players.detect import DETECT_COLUMNS, TRACK_ID_STRIDE
from pickleball_ml.players.identify import (
    PLAYER_TRACK_COLUMNS,
    IdentifyRunConfig,
    identify,
)
from pickleball_ml.players.identity import IdentityConfig
from pickleball_ml.video.steady_part import ExcludedPart, SteadyPart

#: Detect samples 15 times a second; at 30 fps that is every other frame.
FPS = 30.0
RATE_HZ = 15.0
#: Image pixels are court feet, so a row's `image_x`, `image_y` is where it lands.
PIXELS_ARE_FEET = np.eye(3)
#: A court 1 ft to the left of the image: projection must use the homography given.
SHIFTED = np.array([[1.0, 0.0, -1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


def shirt(index: int) -> list[float]:
    descriptor = np.zeros(DESCRIPTOR_SIZE)
    descriptor[index] = 1.0
    return descriptor.tolist()


def stand(
    track_id: int, start_s: float, end_s: float, at: tuple[float, float], colour: int,
    *, chunk: int = 0, truncated: bool = False,
) -> list[dict[str, object]]:
    """Detect rows of one person standing at court position `at` from `start_s` to `end_s`.

    The box is a standing person's shape anywhere in the image; Identify takes the
    ground-contact point from `image_x`, `image_y`, as Detect stored it.
    """
    rows = []
    for k in range(round(start_s * RATE_HZ), round(end_s * RATE_HZ)):
        frame = 2 * k
        rows.append({
            "frame_number": frame, "timestamp_ms": frame / FPS * 1000.0,
            "track_id": chunk * TRACK_ID_STRIDE + track_id,
            "x1": 500.0, "y1": 480.0, "x2": 540.0, "y2": 600.0, "confidence": 0.9,
            "image_x": at[0], "image_y": at[1], "truncated": truncated, "in_gate": True,
            "appearance": shirt(colour),
        })
    return rows


def detections(*people: list[dict[str, object]]) -> pd.DataFrame:
    rows = [row for person in people for row in person]
    return pd.DataFrame(rows, columns=DETECT_COLUMNS)


def number_of_colour(tracks: pd.DataFrame, frame: pd.DataFrame) -> dict[int, set[int]]:
    """Player numbers given to each shirt colour."""
    colours = frame.set_index(["frame_number", "track_id"])["appearance"].map(np.argmax)
    keys = pd.MultiIndex.from_frame(tracks[["frame_number", "track_id"]])
    tracked = tracks.assign(colour=colours.reindex(keys).to_numpy())
    return {int(c): set(g) for c, g in tracked.groupby("colour")["player_number"]}


def four_players(start_s: float, end_s: float, *, chunk: int = 0) -> list[list[dict[str, object]]]:
    """Team of shirts 1-2 near, 3-4 far, each in their own left then right slot."""
    return [
        stand(1, start_s, end_s, (-5, -15), 1, chunk=chunk),
        stand(2, start_s, end_s, (5, -15), 2, chunk=chunk),
        stand(3, start_s, end_s, (5, 12), 3, chunk=chunk),
        stand(4, start_s, end_s, (-5, 12), 4, chunk=chunk),
    ]


def run(frame: pd.DataFrame, homography: np.ndarray = PIXELS_ARE_FEET,
        changeovers_seconds: Sequence[float] = ()) -> pd.DataFrame:
    return identify(frame, homography, IdentityConfig(),
                    changeovers_seconds=changeovers_seconds).tracks


def test_the_output_has_the_player_track_point_columns() -> None:
    tracks = run(detections(*four_players(0, 10)))
    assert list(tracks.columns) == PLAYER_TRACK_COLUMNS
    assert set(tracks["player_number"]) == {1, 2, 3, 4}


def test_ground_contact_points_are_projected_with_the_calibration_given() -> None:
    tracks = run(detections(stand(1, 0, 10, (-5, -15), 1)), homography=SHIFTED)
    assert np.allclose(tracks["court_x"], -6.0)
    assert np.allclose(tracks["court_y"], -15.0)
    assert np.allclose(tracks["image_x"], -5.0)


def test_detects_truncated_flag_is_kept_as_it_was_stored() -> None:
    """Detect decided which boxes the frame's bottom edge cuts off; Identify keeps that
    rather than guessing again from a frame height."""
    frame = detections(stand(1, 0, 10, (-5, -15), 1, truncated=True),
                       stand(2, 0, 10, (5, -15), 2))
    tracks = run(frame)
    assert set(tracks.loc[tracks["track_id"] == 1, "truncated"]) == {True}
    assert set(tracks.loc[tracks["track_id"] == 2, "truncated"]) == {False}


def test_tracks_are_linked_across_a_chunk_boundary() -> None:
    """Detect restarts the tracker every chunk, so every track ends at a boundary and
    the next chunk's ids are new. Each player keeps one number across it."""
    frame = detections(*four_players(0, 300), *four_players(300, 400, chunk=1))
    tracks = run(frame)
    by_track = tracks.groupby("track_id")["player_number"].unique()
    for track_id in (1, 2, 3, 4):
        assert list(by_track[track_id]) == list(by_track[TRACK_ID_STRIDE + track_id])
    assert number_of_colour(tracks, frame) == {1: {1}, 2: {2}, 3: {3}, 4: {4}}


def test_appending_a_chunk_keeps_the_player_numbers() -> None:
    """Identify re-runs on everything Detect has finished each time a chunk is added.
    In the second chunk the partners of each team have swapped slots (as after a serve
    rotation), which must not renumber anyone."""
    first = four_players(0, 300)
    second = [
        stand(1, 300, 600, (5, -15), 1, chunk=1),
        stand(2, 300, 600, (-5, -15), 2, chunk=1),
        stand(3, 300, 600, (-5, 12), 3, chunk=1),
        stand(4, 300, 600, (5, 12), 4, chunk=1),
    ]
    before = detections(*first)
    after = detections(*first, *second)
    assert number_of_colour(run(before), before) == number_of_colour(run(after), after) == {
        1: {1}, 2: {2}, 3: {3}, 4: {4},
    }


def test_a_changeover_beyond_the_tracked_range_is_ignored() -> None:
    """A partial Detect run tracks only part of the match, and a changeover later in it
    has nothing to split yet."""
    result = identify(detections(*four_players(0, 60)), PIXELS_ARE_FEET, IdentityConfig(),
                      changeovers_seconds=(30.0, 900.0))
    assert result.changeovers_seconds == (30.0,)
    assert len(result.diagnostics["games"]) == 2


def test_nothing_detected_is_no_tracks() -> None:
    tracks = run(pd.DataFrame(columns=DETECT_COLUMNS))
    assert tracks.empty
    assert list(tracks.columns) == PLAYER_TRACK_COLUMNS


def test_the_run_config_records_what_identify_read() -> None:
    steady = SteadyPart(10.0, None, (ExcludedPart(0.0, 10.0, 5.0),), "moved")
    config = IdentifyRunConfig(
        identity=IdentityConfig(), detect_chunks=3, tracked_until_seconds=910.0,
        steady_part=steady.to_config(), changeovers_seconds=(400.0,),
    ).to_config()
    assert config["detect_chunks"] == 3
    assert config["tracked_until_seconds"] == pytest.approx(910.0)
    assert config["steady_part"]["excluded"] == [
        {"start_seconds": 0.0, "end_seconds": 10.0, "moved_at_seconds": 5.0}
    ]
    assert config["changeovers_seconds"] == [400.0]
    assert config["identity"]["players_per_side"] == 2
