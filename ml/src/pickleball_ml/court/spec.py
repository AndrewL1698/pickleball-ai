"""Standard pickleball court geometry in court coordinates.

Court coordinate system (see docs/ARCHITECTURE.md):

- Units: feet. Origin: center of the court, under the net.
- Y runs baseline to baseline; negative Y is the near (camera) half.
- X runs sideline to sideline; positive X is to the right for someone standing
  on the near baseline facing the net.
"""

from typing import Final

HALF_WIDTH_FT: Final = 10.0
HALF_LENGTH_FT: Final = 22.0
KITCHEN_DEPTH_FT: Final = 7.0

Point = tuple[float, float]
Segment = tuple[Point, Point]

# Painted ground-plane intersections usable for calibration, in click order.
LANDMARKS: Final[dict[str, Point]] = {
    "near_left_baseline_corner": (-HALF_WIDTH_FT, -HALF_LENGTH_FT),
    "near_center_baseline": (0.0, -HALF_LENGTH_FT),
    "near_right_baseline_corner": (HALF_WIDTH_FT, -HALF_LENGTH_FT),
    "near_left_kitchen": (-HALF_WIDTH_FT, -KITCHEN_DEPTH_FT),
    "near_center_kitchen": (0.0, -KITCHEN_DEPTH_FT),
    "near_right_kitchen": (HALF_WIDTH_FT, -KITCHEN_DEPTH_FT),
    "far_left_kitchen": (-HALF_WIDTH_FT, KITCHEN_DEPTH_FT),
    "far_center_kitchen": (0.0, KITCHEN_DEPTH_FT),
    "far_right_kitchen": (HALF_WIDTH_FT, KITCHEN_DEPTH_FT),
    "far_left_baseline_corner": (-HALF_WIDTH_FT, HALF_LENGTH_FT),
    "far_center_baseline": (0.0, HALF_LENGTH_FT),
    "far_right_baseline_corner": (HALF_WIDTH_FT, HALF_LENGTH_FT),
}


def court_lines() -> dict[str, Segment]:
    """Painted court lines plus the net line, as named segments."""
    w, h, k = HALF_WIDTH_FT, HALF_LENGTH_FT, KITCHEN_DEPTH_FT
    return {
        "left_sideline": ((-w, -h), (-w, h)),
        "right_sideline": ((w, -h), (w, h)),
        "near_baseline": ((-w, -h), (w, -h)),
        "far_baseline": ((-w, h), (w, h)),
        "near_kitchen_line": ((-w, -k), (w, -k)),
        "far_kitchen_line": ((-w, k), (w, k)),
        "near_center_line": ((0.0, -h), (0.0, -k)),
        "far_center_line": ((0.0, k), (0.0, h)),
        "net": ((-w, 0.0), (w, 0.0)),
    }
