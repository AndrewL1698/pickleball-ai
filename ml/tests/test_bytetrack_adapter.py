"""The real ByteTrack behind the `Tracker` protocol, fed boxes rather than a model."""

from types import SimpleNamespace

import numpy as np
import pytest

from pickleball_ml.players.detect import ByteTracker, DetectConfig

byte_tracker = pytest.importorskip("ultralytics.trackers.byte_tracker")

IMAGE = np.zeros((480, 640, 3), dtype=np.uint8)


def new_tracker() -> ByteTracker:
    settings = SimpleNamespace(**DetectConfig().tracker)
    return ByteTracker(byte_tracker.BYTETracker(settings))


def boxes(frame: int) -> np.ndarray:
    """Two people walking slowly apart."""
    return np.array(
        [[100.0 + frame, 100.0, 150.0 + frame, 250.0], [400.0 - frame, 120.0, 450.0 - frame, 260.0]]
    )


def test_people_who_move_a_little_keep_their_track_ids() -> None:
    tracker = new_tracker()
    ids = [tracker.update(boxes(f), np.array([0.9, 0.8]), IMAGE) for f in range(6)]
    # Confirmed from the second frame on, and the same from then.
    later = {tuple(frame_ids) for frame_ids in ids[1:]}
    assert len(later) == 1
    first, second = later.pop()
    assert first > 0 and second > 0 and first != second


def test_each_new_tracker_numbers_its_tracks_from_the_start() -> None:
    runs = []
    for _ in range(2):
        tracker = new_tracker()
        confidence = np.array([0.9, 0.8])
        runs.append([tuple(tracker.update(boxes(f), confidence, IMAGE)) for f in range(3)])
    assert runs[0] == runs[1]


def test_a_box_too_faint_to_track_and_an_empty_frame_get_no_id() -> None:
    tracker = new_tracker()
    assert tracker.update(boxes(0), np.array([0.9, 0.05]), IMAGE)[1] == -1
    assert len(tracker.update(np.empty((0, 4)), np.empty(0), IMAGE)) == 0
