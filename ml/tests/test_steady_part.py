"""Which part of a video Detect tracks, given where the camera moved."""

import pytest

from pickleball_ml.video.steady_part import (
    CameraMove,
    ExcludedPart,
    UndefinedSteadyPart,
    steady_part,
)

#: A bump at 6:08, as in `riggs`.
RIGGS_MOVE = CameraMove(from_seconds=368.0, to_seconds=386.9)


def test_an_unchecked_video_is_tracked_whole() -> None:
    part = steady_part(None, reference_seconds=None)
    assert (part.start_seconds, part.end_seconds) == (0.0, None)
    assert part.excluded == ()
    assert part.steadiness == "unchecked"


@pytest.mark.parametrize("reference_seconds", [None, 12.5])
def test_a_video_with_no_camera_move_is_tracked_whole(reference_seconds: float | None) -> None:
    part = steady_part((), reference_seconds=reference_seconds)
    assert (part.start_seconds, part.end_seconds) == (0.0, None)
    assert part.excluded == ()
    assert part.steadiness == "steady"


def test_a_reference_frame_before_the_only_move_tracks_up_to_it() -> None:
    part = steady_part((RIGGS_MOVE,), reference_seconds=100.0)
    assert (part.start_seconds, part.end_seconds) == (0.0, 368.0)
    assert part.excluded == (
        ExcludedPart(start_seconds=368.0, end_seconds=None, moved_at_seconds=368.0),
    )
    assert part.steadiness == "moved"


def test_a_reference_frame_after_the_only_move_tracks_from_where_it_settled() -> None:
    part = steady_part((RIGGS_MOVE,), reference_seconds=500.0)
    assert (part.start_seconds, part.end_seconds) == (386.9, None)
    assert part.excluded == (
        ExcludedPart(start_seconds=0.0, end_seconds=386.9, moved_at_seconds=368.0),
    )


MOVES = (
    CameraMove(from_seconds=100.0, to_seconds=110.0),
    CameraMove(from_seconds=300.0, to_seconds=320.0),
    CameraMove(from_seconds=600.0, to_seconds=610.0),
)


@pytest.mark.parametrize(
    ("reference_seconds", "expected_range", "expected_excluded"),
    [
        (50.0, (0.0, 100.0), [(100.0, None, 100.0)]),
        (200.0, (110.0, 300.0), [(0.0, 110.0, 100.0), (300.0, None, 300.0)]),
        (400.0, (320.0, 600.0), [(0.0, 320.0, 300.0), (600.0, None, 600.0)]),
        (900.0, (610.0, None), [(0.0, 610.0, 600.0)]),
    ],
)
def test_with_several_moves_the_part_holding_the_reference_frame_is_tracked(
    reference_seconds: float,
    expected_range: tuple[float, float | None],
    expected_excluded: list[tuple[float, float | None, float]],
) -> None:
    part = steady_part(MOVES, reference_seconds=reference_seconds)
    assert (part.start_seconds, part.end_seconds) == expected_range
    assert part.excluded == tuple(ExcludedPart(*values) for values in expected_excluded)


@pytest.mark.parametrize(
    ("reference_seconds", "expected_range"),
    [
        # A sample on a boundary belongs to the part it bounds.
        (100.0, (0.0, 100.0)),
        (110.0, (110.0, 300.0)),
        (0.0, (0.0, 100.0)),
    ],
)
def test_a_reference_frame_on_a_boundary_belongs_to_the_part_it_bounds(
    reference_seconds: float, expected_range: tuple[float, float | None]
) -> None:
    part = steady_part(MOVES, reference_seconds=reference_seconds)
    assert (part.start_seconds, part.end_seconds) == expected_range


def test_moves_are_taken_in_time_order_whatever_order_they_arrive_in() -> None:
    part = steady_part(tuple(reversed(MOVES)), reference_seconds=200.0)
    assert (part.start_seconds, part.end_seconds) == (110.0, 300.0)


def test_a_background_calibration_on_a_moved_camera_has_no_steady_part() -> None:
    """The background is a median across every position the camera held."""
    with pytest.raises(UndefinedSteadyPart):
        steady_part((RIGGS_MOVE,), reference_seconds=None)


def test_a_reference_frame_taken_while_the_camera_was_moving_has_no_steady_part() -> None:
    with pytest.raises(UndefinedSteadyPart):
        steady_part((RIGGS_MOVE,), reference_seconds=375.0)


def test_the_part_is_recorded_as_plain_values() -> None:
    part = steady_part((RIGGS_MOVE,), reference_seconds=100.0)
    assert part.to_config() == {
        "start_seconds": 0.0,
        "end_seconds": 368.0,
        "excluded": [{"start_seconds": 368.0, "end_seconds": None, "moved_at_seconds": 368.0}],
        "steadiness": "moved",
    }
