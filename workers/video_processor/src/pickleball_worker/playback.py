"""The playback copy: an H.264 MP4 of the video that every browser can play and seek.

Phone uploads are often HEVC `.mov`, which not every browser plays. The copy is
for watching only; the CV stages always read the original.

What the copy keeps, because the web app lines it up against the original:

- **Timestamps.** No frame-rate conversion (`-fps_mode passthrough`): phone video
  is often variable frame rate, Detect and Identify timestamp frames from the
  original, and the top-down court follows the copy's `currentTime`. A second
  in the copy is the same second in the original.
- **Display orientation.** ffmpeg's automatic rotation stays on, so the copy is
  upright with no rotation tag, the same way round as `Video.width`/`height`.

The shorter side is scaled to 720 pixels, or left alone if it is already
shorter. ffmpeg is the binary the `imageio-ffmpeg` dependency ships, never one
found on `PATH`, so CI, Docker and a laptop all encode with the same build.

Both the preparation job and `pbworker-backfill-playback` call
`make_playback_copy`. Neither treats a failure as fatal to anything else.
"""

import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import imageio_ffmpeg

from pickleball_api.storage import CHUNK_BYTES, Storage, StorageError, new_storage_key

#: The shorter side of a playback copy, unless the source is already smaller.
PLAYBACK_SHORT_SIDE = 720

#: Far past any copy a match makes (720p H.264 is a few GB an hour); the limit is
#: only there because `write` asks.
MAX_PLAYBACK_BYTES = 64 * 1024 * 1024 * 1024

#: How much of ffmpeg's error output a failure carries into the log.
_STDERR_TAIL_BYTES = 2000

#: Called with how far through the encode is, 0.0 to 1.0.
TranscodeProgress = Callable[[float], None]


class PlaybackCopyFailed(Exception):
    """The copy could not be made or stored. The message is for the server log only."""


def ffmpeg_exe() -> str:
    """The bundled ffmpeg binary. A function so a test can point it elsewhere."""
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def playback_size(width: int, height: int) -> tuple[int, int]:
    """The copy's size for a video of `width` x `height` in display orientation.

    The shorter side becomes `min(720, shorter side)` and the aspect ratio is
    kept. Both sides are rounded down to even, because `libx264` with `yuv420p`
    refuses odd ones, including those of an odd-sized source left unscaled.
    """
    short, long = min(width, height), max(width, height)
    scale = min(PLAYBACK_SHORT_SIDE, short) / short
    new_short, new_long = _even(round(short * scale)), _even(round(long * scale))
    return (new_short, new_long) if width <= height else (new_long, new_short)


def make_playback_copy(
    storage: Storage,
    source_key: str,
    *,
    width: int,
    height: int,
    duration_seconds: float,
    report: TranscodeProgress | None = None,
) -> str:
    """Transcode the video stored under `source_key` and store the copy.

    `width`, `height` and `duration_seconds` are the source's decoded metadata,
    in display orientation. Returns the copy's new storage key. Raises
    `PlaybackCopyFailed`, leaving nothing new in storage, if the source is gone,
    ffmpeg fails, or the copy cannot be stored.
    """
    target = playback_size(width, height)
    with tempfile.TemporaryDirectory(prefix="playback-") as scratch:
        encoded = Path(scratch) / "playback.mp4"
        try:
            with storage.local_path(source_key) as source:
                _transcode(source, encoded, target, duration_seconds, report)
            key = new_storage_key(".mp4")
            storage.write(key, _chunks(encoded), max_bytes=MAX_PLAYBACK_BYTES)
        except (OSError, StorageError) as exc:
            raise PlaybackCopyFailed(f"{type(exc).__name__}: {exc}") from exc
    return key


def _transcode(
    source: Path,
    destination: Path,
    size: tuple[int, int],
    duration_seconds: float,
    report: TranscodeProgress | None,
) -> None:
    try:
        exe = ffmpeg_exe()
    except RuntimeError as exc:  # imageio-ffmpeg found no binary for this platform
        raise PlaybackCopyFailed(str(exc)) from exc
    width, height = size
    command = [
        exe,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel", "error",
        "-i", str(source),
        # The first video stream, and the first audio stream if there is one.
        "-map", "0:v:0",
        "-map", "0:a:0?",
        # Square pixels, so the displayed size is exactly the encoded size.
        "-vf", f"scale={width}:{height},setsar=1",
        "-fps_mode", "passthrough",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        # 8-bit 4:2:0: phone HDR is often 10-bit, which some browsers will not play.
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        # The index first, so a browser can seek before the whole file arrives.
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-y",
        str(destination),
    ]  # fmt: skip
    errors = destination.with_name("ffmpeg.log")
    with errors.open("w+b") as stderr:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=stderr, stdin=subprocess.DEVNULL, text=True
        )
        try:
            assert process.stdout is not None
            _follow_progress(process.stdout, duration_seconds, report)
            returncode = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if returncode != 0:
            stderr.seek(0)
            tail = stderr.read()[-_STDERR_TAIL_BYTES:].decode("utf-8", "replace").strip()
            raise PlaybackCopyFailed(f"ffmpeg exited with {returncode}: {tail}")


def _follow_progress(
    lines: Iterator[str], duration_seconds: float, report: TranscodeProgress | None
) -> None:
    """Read ffmpeg's `-progress` output to the end, reporting a rising fraction.

    Each block of `key=value` lines carries `out_time_us`, how far into the
    video the encode has got, and ends with `progress=continue` or `progress=end`.
    """
    reached = 0.0
    for line in lines:
        if report is None:
            continue
        key, _, value = line.strip().partition("=")
        fraction = reached
        if key == "out_time_us" and duration_seconds > 0:
            try:
                fraction = int(value) / (duration_seconds * 1_000_000)
            except ValueError:  # "N/A" before the first frame
                continue
        elif key == "progress" and value == "end":
            fraction = 1.0
        fraction = min(max(fraction, 0.0), 1.0)
        if fraction > reached:
            reached = fraction
            report(fraction)


def _chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            yield chunk


def _even(pixels: int) -> int:
    return max(2, pixels - pixels % 2)
