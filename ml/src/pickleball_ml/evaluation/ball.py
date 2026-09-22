"""Ball tracking metrics against manually labeled frames.

Label file (`ball_labels.json`, schema "pbml.ball_labels/v1"):

    {
      "schema": "pbml.ball_labels/v1",
      "clip": "buzz", "window": "buzz_a", "video": "data/raw/...mp4",
      "annotator": "...",
      "frames": [
        {"frame_number": 3600, "split": "test", "state": "visible", "xy": [912.0, 604.5]},
        {"frame_number": 3603, "split": "test", "state": "absent"},
        {"frame_number": 3606, "split": "test", "state": "unsure"}
      ]
    }

`visible` means a human could point at the ball in that frame; `absent` means the
ball is genuinely not visible (out of frame, hidden behind a player, or no rally
in progress); `unsure` frames are excluded from every metric, which keeps
ambiguous motion-blurred frames from silently deciding the numbers.

Because partial tracking is expected at this stage, the metrics are:

- coverage: labeled visible frames where the tracker reported a position at all
- recall at a pixel tolerance: visible frames located within that tolerance
- precision at a pixel tolerance: reported positions that are within tolerance,
  counting every position reported on an `absent` frame as wrong
- pixel error: median and 90th percentile over frames where something was
  reported and the ball was visible

Tolerances are in source-image pixels. A ball is roughly 25 px across near the
camera and 6 px across at the far baseline in 1080p behind-baseline footage, so
a single tolerance would flatter the near court and punish the far court; the
report gives several.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

SCHEMA = "pbml.ball_labels/v1"
DEFAULT_TOLERANCES_PX = (5.0, 10.0, 20.0, 40.0)
STATES = ("visible", "absent", "unsure")


@dataclass(frozen=True)
class BallLabel:
    frame_number: int
    split: str
    state: str
    xy: tuple[float, float] | None


def load_ball_labels(path: Path) -> tuple[dict[str, Any], list[BallLabel]]:
    data = json.loads(path.read_text())
    return data, parse_ball_labels(data)


def parse_ball_labels(data: dict[str, Any]) -> list[BallLabel]:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA!r}, got {data.get('schema')!r}")
    labels: list[BallLabel] = []
    seen: set[int] = set()
    for frame in data["frames"]:
        number = int(frame["frame_number"])
        if number in seen:
            raise ValueError(f"frame {number} is labeled twice")
        seen.add(number)
        state = str(frame.get("state", "visible"))
        if state not in STATES:
            raise ValueError(f"frame {number}: unknown state {state!r}")
        xy = frame.get("xy")
        if state == "visible" and xy is None:
            raise ValueError(f"frame {number}: a visible label needs an xy position")
        labels.append(BallLabel(
            frame_number=number, split=str(frame.get("split", "unspecified")), state=state,
            xy=None if xy is None else (float(xy[0]), float(xy[1])),
        ))
    return labels


def evaluate_ball(
    track: pd.DataFrame, labels: list[BallLabel],
    tolerances_px: tuple[float, ...] = DEFAULT_TOLERANCES_PX,
    count_interpolated: bool = False,
) -> dict[str, Any]:
    """Metrics for all labeled frames and per split.

    Interpolated rows are treated as "nothing reported" unless `count_interpolated`,
    because an interpolated point is not an observation of the ball.
    """
    predictions = _predictions(track, count_interpolated)
    splits = sorted({label.split for label in labels} | {"all"})
    report: dict[str, Any] = {
        "tolerances_px": list(tolerances_px),
        "interpolated_counted": count_interpolated,
        "labels_outside_track": sum(label.frame_number not in predictions.index
                                    for label in labels),
    }
    for split in splits:
        chosen = [label for label in labels if split in ("all", label.split)]
        report[split] = _metrics(predictions, chosen, tolerances_px)
    return report


def _predictions(track: pd.DataFrame, count_interpolated: bool) -> pd.DataFrame:
    frame = track.set_index("frame_number")
    visible = frame["visible"].astype(bool)
    if not count_interpolated and "interpolated" in frame:
        visible &= ~frame["interpolated"].astype(bool)
    return frame.assign(reported=visible)


def _metrics(
    predictions: pd.DataFrame, labels: list[BallLabel], tolerances_px: tuple[float, ...]
) -> dict[str, Any]:
    errors: list[float] = []
    reported_on_visible = 0
    reported_on_absent = 0
    visible_labels = [label for label in labels if label.state == "visible"]
    absent_labels = [label for label in labels if label.state == "absent"]

    for label in visible_labels:
        row = predictions.loc[label.frame_number] if label.frame_number in predictions.index \
            else None
        if row is None or not bool(row["reported"]):
            continue
        reported_on_visible += 1
        assert label.xy is not None
        errors.append(float(np.hypot(cast(float, row["x"]) - label.xy[0],
                                     cast(float, row["y"]) - label.xy[1])))
    for label in absent_labels:
        row = predictions.loc[label.frame_number] if label.frame_number in predictions.index \
            else None
        if row is not None and bool(row["reported"]):
            reported_on_absent += 1

    error_array = np.array(errors)
    at_tolerance = {}
    for tolerance in tolerances_px:
        hits = int((error_array <= tolerance).sum()) if error_array.size else 0
        reported = reported_on_visible + reported_on_absent
        at_tolerance[f"{tolerance:g}px"] = {
            "hits": hits,
            "recall": _rate(hits, len(visible_labels)),
            "precision": _rate(hits, reported),
        }
    return {
        "labeled_frames": len(labels),
        "visible_labels": len(visible_labels),
        "absent_labels": len(absent_labels),
        "unsure_labels": sum(label.state == "unsure" for label in labels),
        "reported_on_visible": reported_on_visible,
        "coverage": _rate(reported_on_visible, len(visible_labels)),
        "reported_on_absent": reported_on_absent,
        "false_positive_rate_on_absent": _rate(reported_on_absent, len(absent_labels)),
        "median_error_px": round(float(np.median(error_array)), 1) if error_array.size else None,
        "p90_error_px": round(float(np.percentile(error_array, 90)), 1) if error_array.size
        else None,
        "at_tolerance": at_tolerance,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
