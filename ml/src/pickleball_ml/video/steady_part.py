"""The steady part of a video: the stretch the calibration's camera position holds for.

One homography is right for one camera position only. When the camera check
(`pickleball_ml.court.camera.find_camera_moves`) found moves, the video splits
into steady parts:

    [0, first from], [first to, second from], ..., [last to, end]

The window between a move's `from_seconds` and `to_seconds` belongs to no part:
the camera was somewhere between its two positions. The part Detect tracks is
the one holding the calibration's reference frame, and a reference frame on a
boundary belongs to the part it bounds (a sample at `from_seconds` still shows
the old position, one at `to_seconds` already shows the new one).

A background image is a median over the whole video, so on a moved camera it
shows no single position and its calibration fits no part. That is an error, not
a guess. A video that was never checked is tracked whole.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

#: `unchecked`: the camera check did not run, so the whole video is assumed steady.
#: `steady`: checked, no move found. `moved`: checked, and part of the video is excluded.
Steadiness = Literal["unchecked", "steady", "moved"]


class UndefinedSteadyPart(ValueError):
    """The calibration's reference frame does not belong to one steady part."""


@dataclass(frozen=True)
class CameraMove:
    """In one place at `from_seconds` and in another by `to_seconds`."""

    from_seconds: float
    to_seconds: float


@dataclass(frozen=True)
class ExcludedPart:
    """Video that is not tracked. `end_seconds` None runs to the end of the video.

    `moved_at_seconds` is the move that separates it from the tracked part, so a
    page can say "Not processed: the camera moved at 6:08".
    """

    start_seconds: float
    end_seconds: float | None
    moved_at_seconds: float


@dataclass(frozen=True)
class SteadyPart:
    """The range to track, `[start_seconds, end_seconds)`; `end_seconds` None is the end."""

    start_seconds: float
    end_seconds: float | None
    excluded: tuple[ExcludedPart, ...]
    steadiness: Steadiness

    def to_config(self) -> dict[str, Any]:
        """Plain values, for a run's `config_json`."""
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "excluded": [asdict(part) for part in self.excluded],
            "steadiness": self.steadiness,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SteadyPart":
        return cls(
            start_seconds=float(config["start_seconds"]),
            end_seconds=None if config["end_seconds"] is None else float(config["end_seconds"]),
            excluded=tuple(ExcludedPart(**part) for part in config["excluded"]),
            steadiness=config["steadiness"],
        )


def steady_part(
    moves: Sequence[CameraMove] | None, reference_seconds: float | None
) -> SteadyPart:
    """The part of the video to track.

    `moves` None means the camera was not checked; empty means checked and steady.
    `reference_seconds` is where the calibration's image came from, or None for a
    background. Raises `UndefinedSteadyPart` when the camera moved and the image
    is a background, or was taken while the camera was moving.
    """
    if moves is None:
        return SteadyPart(0.0, None, (), "unchecked")
    if not moves:
        return SteadyPart(0.0, None, (), "steady")
    if reference_seconds is None:
        raise UndefinedSteadyPart("a background image fits no single camera position")

    ordered = sorted(moves, key=lambda move: move.from_seconds)
    # Part i runs from the end of move i-1 (or 0) to the start of move i (or the end).
    starts = [0.0, *(move.to_seconds for move in ordered)]
    ends: list[float | None] = [*(move.from_seconds for move in ordered), None]
    for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if start <= reference_seconds and (end is None or reference_seconds <= end):
            excluded: list[ExcludedPart] = []
            if index > 0:
                excluded.append(ExcludedPart(0.0, start, ordered[index - 1].from_seconds))
            if end is not None:
                excluded.append(ExcludedPart(end, None, end))
            return SteadyPart(start, end, tuple(excluded), "moved")
    raise UndefinedSteadyPart(f"the camera was moving at {reference_seconds:.1f} s")
