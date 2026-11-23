"""Error of predicted player ground-contact points against hand-clicked ones.

Label file (`data/annotations/<clip>/ground_contact_labels.json`, schema
"pbml.ground_contact_labels/v1"), one per clip, written by `pbml label-ground-contact`:

    {
      "schema": "pbml.ground_contact_labels/v1",
      "clip": "buzz", "video": "data/raw/...mp4", "annotator": "...",
      "frames": [
        {"frame_number": 3600, "window": "buzz_a", "split": "tune",
         "points": [{"xy": [912.0, 604.5]}, {"xy": [400.0, 300.0], "unsure": true}]},
        {"frame_number": 3690, "window": "buzz_a", "split": "tune", "skip": true}
      ]
    }

A labeled frame is complete: every court player visible in it has a point, so a
predicted player with no label nearby is an extra prediction, not an unlabeled one.
A point is `unsure` when the feet are hidden (behind another player, below the
frame edge) or off the ground, so the click is a guess; it still absorbs a nearby
prediction but is left out of every error figure. A `skip` frame was not labeled.
Frames are keyed by window and frame number, so overlapping windows of a clip can
each label the same frame.

Predicted and labeled points are matched one-to-one within each frame; a pair is
allowed when the points are at most half the predicted box's height apart, so the
gate scales with the player's apparent size. Errors are reported in source-image
pixels and in court feet, where both points go through the same homography so the
calibration's own error cancels out. Depth error is the signed court-y difference
(predicted minus labeled): positive means the prediction is farther from the camera.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from pickleball_ml.court.geometry import FloatArray, apply_homography

SCHEMA = "pbml.ground_contact_labels/v1"
MAX_MATCH_BOX_HEIGHTS = 0.5
Side = Literal["near", "far"]


@dataclass(frozen=True)
class GroundContactPoint:
    xy: tuple[float, float]
    unsure: bool = False


@dataclass(frozen=True)
class GroundContactFrame:
    """Every visible court player's ground-contact point in one labeled frame."""

    frame_number: int
    window: str
    split: str
    points: tuple[GroundContactPoint, ...]


def load_ground_contact_labels(path: Path) -> tuple[dict[str, Any], list[GroundContactFrame]]:
    """File header and its labeled frames."""
    data = json.loads(path.read_text())
    return data, parse_ground_contact_labels(data)


def parse_ground_contact_labels(data: dict[str, Any]) -> list[GroundContactFrame]:
    """Labeled frames of a label file; `skip` frames are left out."""
    if data.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA!r}, got {data.get('schema')!r}")
    frames: list[GroundContactFrame] = []
    seen: set[tuple[str, int]] = set()
    for raw in data["frames"]:
        number = int(raw["frame_number"])
        window = str(raw.get("window", ""))
        if (window, number) in seen:
            raise ValueError(f"frame {number} of window {window!r} is labeled twice")
        seen.add((window, number))
        if raw.get("skip"):
            continue
        points = []
        for point in raw.get("points", []):
            xy = point.get("xy")
            if xy is None:
                raise ValueError(f"frame {number}: every point needs an xy position")
            points.append(GroundContactPoint(xy=(float(xy[0]), float(xy[1])),
                                             unsure=bool(point.get("unsure", False))))
        frames.append(GroundContactFrame(
            frame_number=number, window=window,
            split=str(raw.get("split", "unspecified")), points=tuple(points),
        ))
    return frames


@dataclass(frozen=True)
class _Error:
    px: float
    ft: float
    depth_ft: float


@dataclass(frozen=True)
class _Outcome:
    """One labeled point: its side, the matched prediction's truncation, and its error.

    `truncated` is None when nothing matched. `error` is None when nothing matched or
    the point is unsure.
    """

    side: Side
    truncated: bool | None
    unsure: bool
    error: _Error | None


def evaluate_ground_contact(
    players: pd.DataFrame, labels: list[GroundContactFrame], homography: FloatArray,
    max_match_box_heights: float = MAX_MATCH_BOX_HEIGHTS,
) -> dict[str, Any]:
    """Error of predicted player ground-contact points against labeled ones.

    `players` are player track rows (`frame_number`, `image_x`, `image_y`, `y1`, `y2`,
    `truncated`). The report has the number of extra predictions (predicted players in
    labeled frames that match no label) and metrics for these groups of labeled points:
    all; near and far (the label's side of the court); truncated and not_truncated
    (whether the matched prediction's box is cut off by the bottom of the frame). Only
    matched points have a truncation, so those two groups never count a miss; their
    unsure counts show how many such predictions could not be checked.
    """
    by_frame = {cast(int, f): g for f, g in players.groupby("frame_number")}
    outcomes: list[_Outcome] = []
    extras = 0
    for frame in labels:
        rows = by_frame.get(frame.frame_number)
        if rows is None:
            predicted = np.empty((0, 3))
            truncated = np.zeros(0, dtype=bool)
        else:
            predicted = np.column_stack([rows["image_x"], rows["image_y"],
                                         rows["y2"] - rows["y1"]]).astype(np.float64)
            truncated = rows["truncated"].to_numpy(dtype=bool)
        pairs = match_points([p.xy for p in frame.points], predicted, max_match_box_heights)
        extras += len(predicted) - sum(j is not None for j in pairs)
        for label, j in zip(frame.points, pairs, strict=True):
            label_court = apply_homography(homography, np.array([label.xy]))[0]
            side: Side = "near" if label_court[1] < 0 else "far"
            error = None
            if j is not None and not label.unsure:
                court = apply_homography(homography, predicted[j:j + 1, :2])[0]
                error = _Error(
                    px=float(np.hypot(*(predicted[j, :2] - np.array(label.xy)))),
                    ft=float(np.hypot(*(court - label_court))),
                    depth_ft=float(court[1] - label_court[1]),
                )
            outcomes.append(_Outcome(
                side=side, truncated=None if j is None else bool(truncated[j]),
                unsure=label.unsure, error=error,
            ))
    return {
        "extra_predictions": extras,
        "all": _metrics(outcomes),
        "near": _metrics([o for o in outcomes if o.side == "near"]),
        "far": _metrics([o for o in outcomes if o.side == "far"]),
        "truncated": _metrics([o for o in outcomes if o.truncated is True]),
        "not_truncated": _metrics([o for o in outcomes if o.truncated is False]),
    }


def match_points(
    labels: list[tuple[float, float]], predicted: FloatArray, max_box_heights: float
) -> list[int | None]:
    """Index of the predicted row matched to each label, greedy and one-to-one.

    `predicted` rows are (x, y, box height). A pair is allowed when the points are at
    most `max_box_heights` box heights apart: a far player is small, so a few pixels
    matter, and a near one is large. Pairs are taken in order of that relative distance.
    """
    candidates = []
    for i, (x, y) in enumerate(labels):
        for j, (px, py, height) in enumerate(predicted):
            relative = float(np.hypot(px - x, py - y)) / max(float(height), 1.0)
            if relative <= max_box_heights:
                candidates.append((relative, i, j))
    matched: list[int | None] = [None] * len(labels)
    used: set[int] = set()
    for _, i, j in sorted(candidates):
        if matched[i] is None and j not in used:
            matched[i] = j
            used.add(j)
    return matched


def _metrics(outcomes: list[_Outcome]) -> dict[str, Any]:
    scored = [o for o in outcomes if not o.unsure]
    errors = [o.error for o in scored if o.error is not None]
    px = [e.px for e in errors]
    ft = [e.ft for e in errors]
    return {
        "labeled_points": len(scored),
        "unsure_points": len(outcomes) - len(scored),
        "matched": len(errors),
        "missed": len(scored) - len(errors),
        "coverage": _rate(len(errors), len(scored)),
        "median_error_px": _percentile(px, 50),
        "p90_error_px": _percentile(px, 90),
        "median_error_ft": _percentile(ft, 50),
        "p90_error_ft": _percentile(ft, 90),
        "median_depth_error_ft": _percentile([e.depth_ft for e in errors], 50),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 2) if values else None
