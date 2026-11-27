"""The playback copy: the transcode both the preparation job and the backfill use.

The encode runs the real bundled ffmpeg on a tiny fixture video, so these tests
check the file a browser would actually get. `probe` is shared with the task and
backfill tests.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import pytest

from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_ml.video.fixtures import write_test_video
from pickleball_ml.video.reader import read_metadata
from pickleball_worker import playback
from pickleball_worker.playback import PlaybackCopyFailed, make_playback_copy, playback_size


@dataclass(frozen=True)
class Probe:
    #: ffmpeg's name for the video codec, e.g. "h264".
    codec: str
    width: int
    height: int
    rotation_degrees: int
    #: Whether ffmpeg itself sees a rotation tag (a display matrix) on the stream.
    has_display_matrix: bool
    frame_count: int
    duration_seconds: float


def probe(path: Path) -> Probe:
    """What a player would see, read with the same bundled ffmpeg and with OpenCV."""
    decoded = read_metadata(path)
    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(path))
    described = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    ).stderr.lower()
    stream = re.search(r"video: (\w+)", described)
    return Probe(
        codec=stream.group(1) if stream else "",
        width=decoded.width,
        height=decoded.height,
        rotation_degrees=decoded.rotation_degrees,
        has_display_matrix="display matrix" in described or "displaymatrix" in described,
        frame_count=frames,
        duration_seconds=seconds,
    )


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "uploads")


@pytest.fixture
def source_key(storage: LocalFileStorage, tmp_path: Path) -> str:
    """64x48 stored, tagged 90 degrees, so 48x64 displayed; 20 frames at 10 fps."""
    content = write_test_video(
        tmp_path / "source.mp4", width=64, height=48, frames=20, fps=10.0, rotation=90
    ).read_bytes()
    key = new_storage_key(".mp4")
    storage.write(key, [content], max_bytes=len(content))
    return key


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ((1920, 1080), (1280, 720)),  # landscape HD
        ((3840, 2160), (1280, 720)),  # 4K
        ((1080, 1920), (720, 1280)),  # portrait
        ((1280, 720), (1280, 720)),  # already 720p
        ((48, 64), (48, 64)),  # smaller than 720p: never upscaled
        ((65, 49), (64, 48)),  # odd and unscaled: rounded down to even
        ((1440, 1080), (960, 720)),  # 4:3
        ((1918, 1078), (1280, 720)),  # odd shorter side, scaled
    ],
)
def test_the_shorter_side_becomes_720_or_less_and_both_sides_are_even(
    display: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert playback_size(*display) == expected


def test_a_copy_is_h264_upright_with_the_sources_frames_and_timing(
    storage: LocalFileStorage, source_key: str
) -> None:
    key = make_playback_copy(storage, source_key, width=48, height=64, duration_seconds=2.0)

    assert key != source_key and key.endswith(".mp4")
    with storage.local_path(key) as path:
        copy = probe(path)
    with storage.local_path(source_key) as path:
        source = probe(path)
    assert copy.codec == "h264"
    assert (copy.width, copy.height) == (48, 64)
    assert copy.rotation_degrees == 0 and not copy.has_display_matrix
    assert source.has_display_matrix  # the check above can see a tag when there is one
    assert copy.frame_count == source.frame_count == 20
    assert copy.duration_seconds == pytest.approx(source.duration_seconds, abs=0.1)


def test_progress_is_reported_as_a_rising_fraction(
    storage: LocalFileStorage, source_key: str
) -> None:
    seen: list[float] = []
    make_playback_copy(
        storage, source_key, width=48, height=64, duration_seconds=2.0, report=seen.append
    )
    assert seen, "no progress was reported"
    assert seen == sorted(seen)
    assert all(0.0 <= fraction <= 1.0 for fraction in seen)


def test_a_missing_ffmpeg_fails_the_copy_and_leaves_nothing_behind(
    storage: LocalFileStorage, source_key: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(playback, "ffmpeg_exe", lambda: str(tmp_path / "no-such-ffmpeg"))
    with pytest.raises(PlaybackCopyFailed):
        make_playback_copy(storage, source_key, width=48, height=64, duration_seconds=2.0)
    assert [p.name for p in storage.root.iterdir()] == [source_key]


def test_a_source_ffmpeg_cannot_decode_fails_the_copy(storage: LocalFileStorage) -> None:
    key = new_storage_key(".mp4")
    storage.write(key, [b"not a video"], max_bytes=11)
    with pytest.raises(PlaybackCopyFailed):
        make_playback_copy(storage, key, width=48, height=64, duration_seconds=2.0)
    assert [p.name for p in storage.root.iterdir()] == [key]


def test_a_source_missing_from_storage_fails_the_copy(storage: LocalFileStorage) -> None:
    with pytest.raises(PlaybackCopyFailed):
        make_playback_copy(
            storage, new_storage_key(".mp4"), width=48, height=64, duration_seconds=2.0
        )


def test_a_copy_too_large_to_store_fails_and_leaves_nothing_behind(
    storage: LocalFileStorage, source_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playback, "MAX_PLAYBACK_BYTES", 10)
    with pytest.raises(PlaybackCopyFailed):
        make_playback_copy(storage, source_key, width=48, height=64, duration_seconds=2.0)
    assert [p.name for p in storage.root.iterdir()] == [source_key]
