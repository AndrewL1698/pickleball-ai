from pathlib import Path

import cv2
import numpy as np
import pytest

from pickleball_ml.video.reader import iter_frames, read_frame, read_metadata

FPS = 10
FRAMES = 20


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic clip whose frame index is encoded in pixel brightness."""
    path = tmp_path_factory.mktemp("video") / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (64, 48))
    for i in range(FRAMES):
        writer.write(np.full((48, 64, 3), i * 12, dtype=np.uint8))
    writer.release()
    return path


def brightness_index(image: np.ndarray) -> int:
    return round(float(image.mean()) / 12)


def test_metadata(video: Path) -> None:
    meta = read_metadata(video)
    assert (meta.width, meta.height) == (64, 48)
    assert meta.fps == pytest.approx(FPS)
    assert meta.frame_count == FRAMES
    assert meta.duration_seconds == pytest.approx(FRAMES / FPS)


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
