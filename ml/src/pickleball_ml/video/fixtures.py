"""Tiny deterministic video files, for tests that need a real decode.

Lives in the package rather than a test directory because the worker's test
suite needs the same files and can only share code through an installed
package. It writes a few kilobytes with OpenCV and imports nothing heavier.

Rotation is set by patching the track header's display matrix, which is where
a phone records it. OpenCV cannot write the tag itself, and there is no ffmpeg
binary to rely on.
"""

import struct
from pathlib import Path

import cv2
import numpy as np

_ONE = 0x00010000  # 1.0 in 16.16 fixed point
_MINUS_ONE = 0xFFFF0000  # -1.0
_W = 0x40000000  # 1.0 in 2.30 fixed point

#: (a, b, c, d) of the ISO base-media display matrix for each rotation.
_MATRICES = {
    0: (_ONE, 0, 0, _ONE),
    90: (0, _ONE, _MINUS_ONE, 0),
    180: (_MINUS_ONE, 0, 0, _MINUS_ONE),
    270: (0, _MINUS_ONE, _ONE, 0),
}


def write_test_video(
    path: Path,
    *,
    width: int = 64,
    height: int = 48,
    frames: int = 20,
    fps: float = 10.0,
    rotation: int = 0,
) -> Path:
    """Write a small mp4 whose frame i has brightness 12 * i, stored at
    `width` x `height` and tagged with `rotation` degrees."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (width, height))
    try:
        for i in range(frames):
            writer.write(np.full((height, width, 3), (i * 12) % 256, dtype=np.uint8))
    finally:
        writer.release()
    if rotation:
        set_rotation(path, rotation)
    return path


def set_rotation(path: Path, rotation: int) -> None:
    """Rewrite the first track header's display matrix to `rotation` degrees."""
    a, b, c, d = _MATRICES[rotation]
    data = bytearray(path.read_bytes())
    offset = _find_box(data, b"tkhd", 0, len(data))
    if offset is None:
        raise ValueError(f"{path} has no track header")
    version = data[offset + 8]
    # size, type, version+flags, then times and ids whose width depends on the
    # version, then reserved/layer/group/volume fields, then the matrix.
    matrix_at = offset + (48 if version == 0 else 60)
    struct.pack_into(">9I", data, matrix_at, a, b, 0, c, d, 0, 0, 0, _W)
    path.write_bytes(bytes(data))


def _find_box(data: bytearray, name: bytes, start: int, end: int) -> int | None:
    position = start
    while position + 8 <= end:
        size, kind = struct.unpack(">I4s", data[position : position + 8])
        if size < 8:
            return None
        if kind == name:
            return position
        if kind in (b"moov", b"trak"):
            found = _find_box(data, name, position + 8, position + size)
            if found is not None:
                return found
        position += size
    return None
