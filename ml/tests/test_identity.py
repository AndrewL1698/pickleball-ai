import numpy as np
import pandas as pd

from pickleball_ml.players.appearance import DESCRIPTOR_SIZE
from pickleball_ml.players.identity import (
    IdentityConfig,
    resolve_identities,
    split_tracklets,
)
from pickleball_ml.players.tracking import RAW_COLUMNS

FPS = 60.0
STRIDE = 2
CONFIG = IdentityConfig()


def color(index: int) -> list[float]:
    """A distinct one-hot clothing color."""
    descriptor = np.zeros(DESCRIPTOR_SIZE)
    descriptor[index] = 1.0
    return descriptor.tolist()


def walk(track_id: int, frames: range, start: tuple[float, float], end: tuple[float, float],
         shirt: int, height_px: float = 120.0) -> list[dict[str, object]]:
    """Detections of one person walking in a straight line between court positions."""
    rows = []
    n = max(len(frames) - 1, 1)
    for i, frame in enumerate(frames):
        x = start[0] + (end[0] - start[0]) * i / n
        y = start[1] + (end[1] - start[1]) * i / n
        rows.append({
            "frame_number": frame, "timestamp_ms": frame / FPS * 1000, "track_id": track_id,
            "x1": 500.0, "y1": 600.0 - height_px, "x2": 540.0, "y2": 600.0, "confidence": 0.9,
            "court_x": x, "court_y": y, "in_gate": True, "appearance": color(shirt),
        })
    return rows


def resolve(rows: list[dict[str, object]]) -> pd.DataFrame:
    players, _ = resolve_identities(pd.DataFrame(rows, columns=RAW_COLUMNS), FPS, STRIDE,
                                       frame_height=1080, config=CONFIG)
    return players


def four_players(frames: range) -> list[dict[str, object]]:
    return (walk(1, frames, (-5, -15), (-5, -15), shirt=1)
            + walk(2, frames, (5, -15), (5, -15), shirt=2)
            + walk(3, frames, (-5, 12), (-5, 12), shirt=3)
            + walk(4, frames, (5, 12), (5, 12), shirt=4))


def test_brief_tracking_loss_keeps_the_same_player() -> None:
    rows = walk(1, range(0, 200, STRIDE), (-5, -15), (-3, -14), shirt=1)
    # Tracker loses the player for 0.2 s and assigns a new track ID.
    rows += walk(9, range(212, 400, STRIDE), (-3, -14), (-1, -13), shirt=1)
    rows += walk(2, range(0, 400, STRIDE), (5, -15), (5, -15), shirt=2)
    players = resolve(rows)
    ids = players.groupby("track_id")["player_id"].unique()
    assert len(ids[1]) == 1 and len(ids[9]) == 1
    assert ids[1][0] == ids[9][0]
    assert ids[2][0] != ids[1][0]


def test_track_jumping_to_a_neighboring_court_is_split_and_dropped() -> None:
    frames = range(0, 300, STRIDE)
    rows = four_players(frames)
    # Track 3 follows the far-left player, then jumps to someone 20 ft behind the far baseline.
    rows = [r for r in rows if not (r["track_id"] == 3 and int(str(r["frame_number"])) >= 150)]
    rows += walk(3, range(150, 300, STRIDE), (-6, 42), (-6, 42), shirt=7)
    # The real far-left player gets a new track ID.
    rows += walk(8, range(152, 300, STRIDE), (-5, 12), (-5, 12), shirt=3)
    players = resolve(rows)
    assert players["court_y"].max() < 30
    far_left = players[(players["frame_number"] >= 160) & (players["court_x"] < 0)
                       & (players["court_y"] > 0)]
    assert set(far_left["track_id"]) == {8}
    assert far_left["player_id"].nunique() == 1
    before = players[(players["track_id"] == 3)]["player_id"].unique()
    assert list(before) == list(far_left["player_id"].unique())


def test_extra_person_behind_the_baseline_is_not_a_player() -> None:
    frames = range(0, 300, STRIDE)
    rows = four_players(frames)
    rows += walk(5, frames, (-8, 30), (-8, 30), shirt=6)  # standing 8 ft behind far baseline
    players = resolve(rows)
    assert 5 not in set(players["track_id"])
    assert players.groupby("frame_number").size().eq(4).all()


def test_clothing_color_resolves_who_is_who_after_both_leave_frame() -> None:
    rows = walk(1, range(0, 200, STRIDE), (-5, -20), (-4, -24), shirt=1)
    rows += walk(2, range(0, 200, STRIDE), (4, -20), (3, -24), shirt=2)
    # Both leave frame for 5 s and come back having crossed paths behind the baseline.
    rows += walk(11, range(500, 700, STRIDE), (3, -24), (4, -20), shirt=1)
    rows += walk(12, range(500, 700, STRIDE), (-4, -24), (-5, -20), shirt=2)
    players = resolve(rows)
    ids = players.groupby("track_id")["player_id"].first()
    assert ids[11] == ids[1]
    assert ids[12] == ids[2]


def test_identities_are_numbered_by_team() -> None:
    players = resolve(four_players(range(0, 100, STRIDE)))
    teams = players.groupby("player_id")["team"].first().to_dict()
    assert teams == {1: "near", 2: "near", 3: "far", 4: "far"}
    slots = players.groupby("track_id")["slot"].first().to_dict()
    assert slots == {1: "near_left", 2: "near_right", 3: "far_left", 4: "far_right"}


def test_split_on_sudden_box_size_change() -> None:
    rows = walk(1, range(0, 40, STRIDE), (0, 10), (0, 10), shirt=1, height_px=60)
    rows += walk(1, range(40, 80, STRIDE), (0, 10), (0, 10), shirt=1, height_px=140)
    split = split_tracklets(pd.DataFrame(rows, columns=RAW_COLUMNS), FPS, STRIDE, CONFIG)
    assert split["segment_id"].nunique() == 2


def test_tracker_id_handed_to_partner_is_split_by_clothing_color() -> None:
    # Partners stand side by side; at frame 300 the tracker swaps their IDs without any jump.
    rows = walk(1, range(0, 300, STRIDE), (-1, -24), (-1, -24), shirt=1)
    rows += walk(1, range(300, 600, STRIDE), (1, -24), (1, -24), shirt=2)
    rows += walk(2, range(0, 300, STRIDE), (1, -24), (1, -24), shirt=2)
    rows += walk(2, range(300, 600, STRIDE), (-1, -24), (-1, -24), shirt=1)
    players = resolve(rows)
    shirt_of_player = players.assign(shirt=np.where(
        players["frame_number"] < 300, players["track_id"], 3 - players["track_id"]
    )).groupby("player_id")["shirt"].nunique()
    assert shirt_of_player.eq(1).all()


def test_duplicate_track_overlapping_both_partners_is_resolved_moment_by_moment() -> None:
    # Partners stand together; the tracker starts a duplicate track (3) on player B, drops B's
    # original track (2), and track 3 carries on with B. Neither whole track may be discarded.
    rows = walk(1, range(0, 400, STRIDE), (-2, -24), (-2, -24), shirt=1)
    rows += walk(2, range(0, 250, STRIDE), (0, -24), (0, -24), shirt=2)
    rows += walk(3, range(200, 400, STRIDE), (0, -24), (6, -20), shirt=2)
    players = resolve(rows)
    assert players.groupby("frame_number").size().eq(2).all()
    late = players[players["frame_number"] >= 300]
    assert set(late["track_id"]) == {1, 3}
    ids = players.groupby("track_id")["player_id"].unique()
    assert len(ids[3]) == 1 and ids[3][0] == ids[2][0] != ids[1][0]


def test_many_short_fragments_do_not_hide_the_true_continuation() -> None:
    rows = walk(1, range(0, 100, STRIDE), (-5, 12), (-5, 12), shirt=1)
    rows += walk(2, range(0, 400, STRIDE), (5, 12), (5, 12), shirt=2)
    for i in range(15):  # flickering detections of a spectator near the far baseline
        frame = 104 + 4 * i
        rows += walk(100 + i, range(frame, frame + 2, STRIDE), (-8, 26), (-8, 26), shirt=5)
    rows += walk(9, range(120, 400, STRIDE), (-5, 13), (-5, 13), shirt=1)
    players = resolve(rows)
    ids = players.groupby("track_id")["player_id"].first()
    assert ids[9] == ids[1]


def test_untracked_detections_are_ignored() -> None:
    rows = walk(-1, range(0, 40, STRIDE), (0, -10), (0, -10), shirt=1)
    assert resolve(rows).empty
