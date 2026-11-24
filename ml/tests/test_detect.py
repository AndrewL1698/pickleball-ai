"""Detect over a steady part, in checkpointed chunks, with a fake detector and tracker."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pickleball_ml.players.detect import (
    DETECT_COLUMNS,
    TRACK_ID_STRIDE,
    DetectChunk,
    DetectConfig,
    detect_chunks,
)
from pickleball_ml.video.fixtures import write_test_video
from pickleball_ml.video.reader import Frame
from pickleball_ml.video.steady_part import SteadyPart

#: An image pixel is a court foot, 20 ft further from the far end than its row, so
#: the gate (court plus margins: |x| <= 16, -36 <= y <= 34) is easy to place.
PIXELS_TO_FEET = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, -20.0], [0.0, 0.0, 1.0]])
#: 64x48 frames: the first box's feet are inside the gate, the second's are 40 ft
#: to the side, and the third, also inside, reaches the bottom edge of the frame.
IN_GATE = (2.0, 10.0, 10.0, 30.0)
OFF_COURT = (36.0, 10.0, 44.0, 30.0)
AT_THE_EDGE = (4.0, 20.0, 12.0, 48.0)


WHOLE_VIDEO = SteadyPart(0.0, None, (), "steady")


class FakeDetector:
    """Three people in every frame, more confident in brighter frames."""

    def __init__(self) -> None:
        self.frames_seen: list[float] = []

    def detect(self, image: Frame) -> tuple[np.ndarray, np.ndarray]:
        self.frames_seen.append(float(image.mean()))
        brightness = float(image.mean()) / 255.0
        xyxy = np.array([IN_GATE, OFF_COURT, AT_THE_EDGE], dtype=np.float64)
        return xyxy, np.array([0.5 + brightness / 4, 0.6, 0.7])


class FakeTracker:
    """Numbers the boxes it is given, in order, starting from 1 for each new tracker."""

    def update(self, xyxy: np.ndarray, confidence: np.ndarray, image: Frame) -> np.ndarray:
        return np.arange(1, len(xyxy) + 1, dtype=np.int64)


def run(
    video: Path,
    *,
    steady: SteadyPart = WHOLE_VIDEO,
    first_chunk: int = 0,
    chunk_seconds: float = 1.0,
    detector: FakeDetector | None = None,
) -> list[DetectChunk]:
    return list(
        detect_chunks(
            video,
            homography=PIXELS_TO_FEET,
            config=DetectConfig(chunk_seconds=chunk_seconds),
            steady=steady,
            first_chunk=first_chunk,
            detector=detector or FakeDetector(),
            new_tracker=FakeTracker,
        )
    )


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """3.5 s at 30 fps."""
    return write_test_video(tmp_path / "clip.mp4", frames=105, fps=30.0)


def test_chunks_cover_the_steady_part_in_order_and_only_the_last_says_so(clip: Path) -> None:
    chunks = run(clip)
    assert [c.index for c in chunks] == [0, 1, 2, 3]
    assert [(c.start_seconds, c.end_seconds) for c in chunks[:3]] == [
        (0.0, 1.0), (1.0, 2.0), (2.0, 3.0)
    ]
    assert chunks[3].start_seconds == 3.0
    # The video ends partway through the last chunk; it ends with the last frame.
    assert 3.4 <= chunks[3].end_seconds < 3.5
    assert [c.is_last for c in chunks] == [False, False, False, True]
    for chunk in chunks:
        seconds = chunk.detections["timestamp_ms"] / 1000.0
        assert seconds.between(chunk.start_seconds, chunk.end_seconds).all()


def test_about_15_frames_a_second_are_detected(clip: Path) -> None:
    detections = pd.concat(c.detections for c in run(clip))
    assert detections["frame_number"].nunique() == 53  # every other frame of 105
    assert set(detections["frame_number"]) == set(range(0, 105, 2))


def test_a_chunk_holds_what_detect_stores_and_no_court_coordinates(clip: Path) -> None:
    chunk = run(clip)[0]
    frame = chunk.detections
    assert list(frame.columns) == DETECT_COLUMNS
    assert "court_x" not in frame.columns
    first = frame[frame["frame_number"] == 0].reset_index(drop=True)
    # The ground-contact point is the bottom centre of the box, in image pixels.
    assert first.loc[0, ["image_x", "image_y"]].tolist() == [6.0, 30.0]
    assert first["in_gate"].tolist() == [True, False, True]
    assert first["truncated"].tolist() == [False, False, True]
    assert first["confidence"].tolist() == pytest.approx([0.5, 0.6, 0.7], abs=0.01)


def test_only_gated_people_are_tracked_and_described(clip: Path) -> None:
    frame = run(clip)[0].detections
    off_court = frame[~frame["in_gate"]]
    assert (off_court["track_id"] == -1).all()
    assert off_court["appearance"].isna().all()
    on_court = frame[frame["in_gate"]]
    assert (on_court["track_id"] > 0).all()
    assert on_court["appearance"].map(len).eq(72).all()


def test_track_ids_are_unique_across_chunks_although_each_tracker_starts_again(
    clip: Path,
) -> None:
    chunks = run(clip)
    ids = [set(c.detections.loc[c.detections["in_gate"], "track_id"]) for c in chunks]
    assert ids[0] == {1, 2}
    assert ids[2] == {2 * TRACK_ID_STRIDE + 1, 2 * TRACK_ID_STRIDE + 2}
    assert not set.intersection(*ids)


def test_a_steady_part_is_tracked_and_nothing_outside_it(clip: Path) -> None:
    chunks = run(clip, steady=SteadyPart(0.5, 2.0, (), "moved"))
    assert [(c.index, c.start_seconds, c.end_seconds) for c in chunks] == [
        (0, 0.5, 1.5), (1, 1.5, 2.0)
    ]
    seconds = pd.concat(c.detections for c in chunks)["timestamp_ms"] / 1000.0
    assert seconds.min() >= 0.5 and seconds.max() < 2.0
    assert chunks[-1].is_last


def test_starting_at_a_later_chunk_gives_exactly_what_a_full_pass_gives_from_there(
    clip: Path,
) -> None:
    full = run(clip)
    detector = FakeDetector()
    resumed = run(clip, first_chunk=2, detector=detector)
    assert [c.index for c in resumed] == [2, 3]
    for whole, again in zip(full[2:], resumed, strict=True):
        assert (whole.start_seconds, whole.end_seconds, whole.is_last) == (
            again.start_seconds, again.end_seconds, again.is_last
        )
        pd.testing.assert_frame_equal(whole.detections, again.detections)
    # Nothing before the first unfinished chunk was handed to the detector.
    assert len(detector.frames_seen) == len(set(pd.concat(c.detections for c in resumed)
                                                ["frame_number"]))


def test_a_steady_part_with_no_frames_left_still_ends_with_a_last_chunk(clip: Path) -> None:
    chunks = run(clip, steady=SteadyPart(10.0, None, (), "moved"))
    assert len(chunks) == 1
    assert chunks[0].is_last and chunks[0].detections.empty
    assert list(chunks[0].detections.columns) == DETECT_COLUMNS


def test_the_config_round_trips_through_plain_values() -> None:
    config = DetectConfig(conf=0.3, chunk_seconds=60.0)
    assert DetectConfig.from_config(config.to_config()) == config


def test_the_tracker_buffer_is_kept_at_two_seconds_whatever_the_rate() -> None:
    """ByteTrack counts its lost-track buffer in processed frames."""
    assert DetectConfig(rate_hz=15.0).tracker["track_buffer"] == 30
    assert DetectConfig(rate_hz=30.0).tracker["track_buffer"] == 60
