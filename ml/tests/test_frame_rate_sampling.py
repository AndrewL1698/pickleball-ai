"""Sampling frames at a rate set in time, not in frames."""

from pathlib import Path

import numpy as np
import pytest

from pickleball_ml.video.fixtures import write_test_video
from pickleball_ml.video.reader import RateSampler, iter_frames_at_rate


def kept(timestamps_ms: list[float], rate_hz: float) -> list[int]:
    sampler = RateSampler(rate_hz)
    return [i for i, t in enumerate(timestamps_ms) if sampler.keep(t)]


def test_at_30_fps_every_other_frame_is_kept() -> None:
    timestamps = [i * 1000 / 30 for i in range(30)]
    assert kept(timestamps, 15.0) == list(range(0, 30, 2))


def test_at_59_94_fps_about_15_frames_a_second_are_kept() -> None:
    timestamps = [i * 1000 / 59.94 for i in range(int(59.94 * 60))]  # one minute
    indices = kept(timestamps, 15.0)
    assert len(indices) == 900
    # Mostly every fourth frame, never further apart than that.
    assert set(np.diff(indices)) <= {3, 4}


def test_timestamps_rounded_to_the_millisecond_still_land_on_their_tick() -> None:
    """Containers often store whole milliseconds: 66.67 ms becomes 66."""
    timestamps = [float(int(i * 1000 / 30)) for i in range(30)]
    assert kept(timestamps, 15.0) == list(range(0, 30, 2))


def test_variable_frame_rate_keeps_the_first_frame_at_or_after_each_tick() -> None:
    # 30 fps, then a stall past two ticks (133.3 and 200 ms), then 60 fps. Ticks
    # fall every 66.7 ms; a missed tick is not made up for twice.
    timestamps = [0.0, 33.3, 66.7, 100.0, 250.0, 266.7, 283.3, 300.0, 316.7, 333.3]
    assert kept(timestamps, 15.0) == [0, 2, 4, 5, 9]


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_sampled_frames_per_second_do_not_depend_on_the_frame_rate(
    tmp_path: Path, fps: float
) -> None:
    seconds = 3
    video = write_test_video(
        tmp_path / f"clip_{fps:.0f}.mp4", frames=int(fps * seconds), fps=fps
    )
    frames = list(iter_frames_at_rate(video, 15.0))
    assert len(frames) == 15 * seconds
    gaps = np.diff([timestamp for _, timestamp, _ in frames])
    assert gaps == pytest.approx(np.full(len(gaps), 1000 / 15), abs=1.0)


def test_a_later_start_yields_exactly_the_frames_a_full_pass_yields_from_there(
    tmp_path: Path,
) -> None:
    """What makes a resumed run identical to an uninterrupted one."""
    video = write_test_video(tmp_path / "clip.mp4", frames=90, fps=30.0)
    full = [(n, t) for n, t, _ in iter_frames_at_rate(video, 15.0)]
    resumed = [(n, t) for n, t, _ in iter_frames_at_rate(video, 15.0, start_seconds=1.1)]
    assert resumed == [(n, t) for n, t in full if t >= 1100.0]


def test_the_range_end_is_exclusive(tmp_path: Path) -> None:
    video = write_test_video(tmp_path / "clip.mp4", frames=90, fps=30.0)
    frames = list(iter_frames_at_rate(video, 15.0, start_seconds=1.0, end_seconds=2.0))
    assert [n for n, _, _ in frames] == list(range(30, 60, 2))


def test_frames_carry_their_image(tmp_path: Path) -> None:
    video = write_test_video(tmp_path / "clip.mp4", frames=10, fps=10.0)
    frame_number, _, image = next(iter_frames_at_rate(video, 5.0, start_seconds=0.2))
    assert frame_number == 2
    assert image.shape == (48, 64, 3)
    assert int(image.mean()) == pytest.approx(24, abs=3)  # brightness 12 * frame


def test_a_rate_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RateSampler(0.0)
