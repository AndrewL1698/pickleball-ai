import math
from pathlib import Path

import numpy as np
import pytest

from pickleball_ml.video.fixtures import write_test_video
from pickleball_ml.video.reader import (
    InvalidVideoMetadata,
    VideoReadError,
    fourcc_to_codec,
    iter_frames,
    metadata_from_properties,
    normalize_rotation,
    read_frame,
    read_metadata,
)

FPS = 10
FRAMES = 20


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic clip whose frame index is encoded in pixel brightness."""
    path = tmp_path_factory.mktemp("video") / "clip.mp4"
    return write_test_video(path, width=64, height=48, frames=FRAMES, fps=FPS)


def brightness_index(image: np.ndarray) -> int:
    return round(float(image.mean()) / 12)


def test_metadata(video: Path) -> None:
    meta = read_metadata(video)
    assert (meta.width, meta.height) == (64, 48)
    assert meta.fps == pytest.approx(FPS)
    assert meta.frame_count == FRAMES
    assert meta.duration_seconds == pytest.approx(FRAMES / FPS)
    assert meta.rotation_degrees == 0
    assert meta.codec  # the writer's FourCC, whatever this OpenCV build calls it


def test_iter_frames_window_and_stride_keep_source_frame_numbers(video: Path) -> None:
    frames = list(iter_frames(video, start_frame=5, end_frame=15, stride=3))
    assert [f for f, _, _ in frames] == [5, 8, 11, 14]
    assert [brightness_index(img) for _, _, img in frames] == [5, 8, 11, 14]
    assert [t for _, t, _ in frames] == pytest.approx([500, 800, 1100, 1400])


def test_iter_frames_stops_at_end_of_video(video: Path) -> None:
    assert [f for f, _, _ in iter_frames(video, start_frame=18)] == [18, 19]


def test_read_frame(video: Path) -> None:
    assert brightness_index(read_frame(video, 7)) == 7
    with pytest.raises(ValueError):
        read_frame(video, FRAMES + 5)


def test_missing_video_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_metadata(tmp_path / "nope.mp4")


VALID = dict(width=64.0, height=48.0, fps=30.0, frame_count=300.0, fourcc=0.0, rotation=0.0)


@pytest.mark.parametrize(
    ("rotation", "size"), [(0, (64, 48)), (90, (48, 64)), (180, (64, 48)), (270, (48, 64))]
)
def test_rotated_video_is_reported_in_display_orientation(
    tmp_path: Path, rotation: int, size: tuple[int, int]
) -> None:
    """A clip stored 64x48 with a 90-degree tag plays as 48x64. Width, height
    and the frames themselves must all agree on display orientation."""
    path = write_test_video(tmp_path / f"r{rotation}.mp4", rotation=rotation)
    meta = read_metadata(path)
    assert meta.rotation_degrees == rotation
    assert (meta.width, meta.height) == size
    frame = read_frame(path, 0)
    assert (frame.shape[1], frame.shape[0]) == size


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.0, 0), (90.0, 90), (180.0, 180), (270.0, 270), (-90.0, 270), (-270.0, 90),
     (360.0, 0), (450.0, 90), (89.9999999, 90)],
)
def test_rotation_is_normalized_to_a_right_angle(raw: float, expected: int) -> None:
    assert normalize_rotation(raw) == expected


@pytest.mark.parametrize("raw", [45.0, 91.0, 90.5, math.nan, math.inf])
def test_a_rotation_that_is_not_a_right_angle_is_refused(raw: float) -> None:
    with pytest.raises(InvalidVideoMetadata):
        normalize_rotation(raw)


def test_valid_properties_become_metadata() -> None:
    meta = metadata_from_properties(Path("m.mp4"), **{**VALID, "rotation": -90.0})
    assert (meta.width, meta.height, meta.frame_count) == (64, 48, 300)
    assert meta.duration_seconds == pytest.approx(10.0)
    assert meta.rotation_degrees == 270


@pytest.mark.parametrize(
    "override",
    [
        {"width": 0.0},
        {"height": -1.0},
        {"width": math.nan},
        {"width": 64.5},
        {"fps": 0.0},
        {"fps": -30.0},
        {"fps": math.nan},
        {"fps": math.inf},
        {"fps": 90_000.0},
        {"frame_count": 0.0},
        {"frame_count": -1.0},
        {"rotation": 45.0},
    ],
)
def test_implausible_properties_are_refused_rather_than_zero_filled(
    override: dict[str, float],
) -> None:
    with pytest.raises(InvalidVideoMetadata):
        metadata_from_properties(Path("m.mp4"), **{**VALID, **override})


def test_invalid_metadata_is_a_read_error() -> None:
    """Callers can treat every "this is not a usable video" the same way."""
    assert issubclass(InvalidVideoMetadata, VideoReadError)
    assert issubclass(VideoReadError, OSError)


@pytest.mark.parametrize(
    ("fourcc", "expected"),
    [
        (float(int.from_bytes(b"avc1", "little")), "avc1"),
        (float(int.from_bytes(b"hvc1", "little")), "hvc1"),
        (0.0, ""),
        (-1.0, ""),
        (math.nan, ""),
        (float(int.from_bytes(b"\x01\x02\x03\x04", "little")), ""),
    ],
)
def test_fourcc_is_decoded_or_left_blank(fourcc: float, expected: str) -> None:
    assert fourcc_to_codec(fourcc) == expected


def test_a_file_that_is_not_a_video_is_a_read_error(tmp_path: Path) -> None:
    path = tmp_path / "fake.mp4"
    path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 4000)
    with pytest.raises(VideoReadError):
        read_metadata(path)


def test_fixture_frames_are_deterministic(tmp_path: Path) -> None:
    first = write_test_video(tmp_path / "a.mp4")
    second = write_test_video(tmp_path / "b.mp4")
    assert first.read_bytes() == second.read_bytes()
    assert np.array_equal(read_frame(first, 3), read_frame(second, 3))
