"""Ground-contact points of person boxes and play-area membership in court coordinates."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from pickleball_ml.court.geometry import FloatArray, apply_homography
from pickleball_ml.court.spec import HALF_LENGTH_FT, HALF_WIDTH_FT

BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class PlayArea:
    """Court rectangle extended by margins, in feet.

    The near and far end margins are separate: with a low or corner camera the
    far end is foreshortened and often borders another court, so it needs a
    tighter margin than the near end.
    """

    side_margin_ft: float
    near_end_margin_ft: float
    far_end_margin_ft: float

    def contains(self, court_x: FloatArray, court_y: FloatArray) -> BoolArray:
        x = np.asarray(court_x, dtype=np.float64)
        y = np.asarray(court_y, dtype=np.float64)
        return np.asarray(
            (np.abs(x) <= HALF_WIDTH_FT + self.side_margin_ft)
            & (y >= -HALF_LENGTH_FT - self.near_end_margin_ft)
            & (y <= HALF_LENGTH_FT + self.far_end_margin_ft)
        )


def foot_points(xyxy: FloatArray) -> FloatArray:
    """Bottom center of each (x1, y1, x2, y2) box: the estimated ground-contact point."""
    boxes = np.asarray(xyxy, dtype=np.float64).reshape(-1, 4)
    return np.column_stack([(boxes[:, 0] + boxes[:, 2]) / 2.0, boxes[:, 3]])


def project_detections(
    detections: pd.DataFrame, homography: FloatArray, frame_height: int, edge_margin_px: int = 2
) -> pd.DataFrame:
    """Add ground-contact image point and its court coordinates to box rows.

    `truncated` marks boxes cut off by the bottom of the frame, whose feet are
    not visible, so the projected position is unreliable.
    """
    out = detections.copy()
    feet = foot_points(out[["x1", "y1", "x2", "y2"]].to_numpy())
    court = apply_homography(homography, feet) if len(out) else np.empty((0, 2))
    out["image_x"] = feet[:, 0]
    out["image_y"] = feet[:, 1]
    out["court_x"] = court[:, 0]
    out["court_y"] = court[:, 1]
    out["truncated"] = out["y2"] >= frame_height - edge_margin_px
    return out
