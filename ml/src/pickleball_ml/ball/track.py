"""Turn per-frame ball candidates into one conservative track.

The detector proposes several candidates per frame, many of them wrong (a shoe,
a line marking, a bright patch of fence). Picking the highest-scoring candidate
frame by frame produces a track that jumps across the image. Instead, one pass
of Viterbi over the processed frames chooses, for each frame, either one
candidate or "missing", trading detection score against how the ball would have
to move:

- a candidate costs its own missing score, and is forbidden outright when
  reaching it from the previous position would exceed `max_speed_px_s`
- once two positions are known, the next one is compared with where constant
  velocity would put it, and pays up to `motion_weight` for deviating further
  than `prediction_tolerance_px`. Penalising deviation from the prediction
  rather than raw displacement is what keeps a candidate sitting where the ball
  *was* from stealing a ball in flight, the usual failure of picking the
  nearest candidate. The penalty is capped, because a hit or a bounce really
  does break the constant-velocity prediction
- being missing costs `miss_cost`, so weak isolated candidates are dropped
- coming back after a gap costs `restart_cost`

Nothing is interpolated by default: a frame the detector could not resolve stays
missing (`visible` false, x/y null). `interpolate_max_gap_frames` can fill short
gaps, and those rows are flagged `interpolated` so analytics can exclude them.

Everything here is deterministic and unit tested; the only learned component is
the detector that produced the candidates.
"""

from dataclasses import asdict, dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

TRACK_COLUMNS = ["frame_number", "timestamp_ms", "x", "y", "score", "visible", "candidate",
                 "interpolated", "speed_px_s"]
INF = float("inf")


@dataclass(frozen=True)
class BallTrackConfig:
    """Post-processing of ball candidates. Scores are on the detector's own scale."""

    min_score: float = 0.05  # candidates below this are not considered at all
    miss_cost: float = 0.15  # score a candidate must beat to be preferred over "missing"
    restart_cost: float = 0.10  # extra cost of becoming visible again after a gap
    motion_weight: float = 0.30  # maximum cost of departing from the predicted position
    prediction_tolerance_px: float = 40.0  # deviation that costs the full motion_weight
    max_speed_px_s: float = 6000.0  # hard gate, ~65 mph near the camera at 1080p
    interpolate_max_gap_frames: int = 0  # 0 = never interpolate


def select_track(
    candidates: pd.DataFrame, frames: list[int], timestamps: dict[int, float], fps: float,
    config: BallTrackConfig,
) -> pd.DataFrame:
    """One row per processed frame: the chosen candidate, or a missing observation."""
    usable = candidates[(candidates["candidate"] >= 0)
                        & (candidates["score"] >= config.min_score)]
    if "in_gate" in usable:
        usable = usable[usable["in_gate"].astype(bool)]
    by_frame: dict[int, np.ndarray] = {
        cast(int, f): g.sort_values("candidate")[["x", "y", "score", "candidate"]]
        .to_numpy(dtype=float)
        for f, g in usable.groupby("frame_number")
    }
    options: list[list[tuple[float, float, float, int]]] = [
        [(float(x), float(y), float(score), int(candidate))
         for x, y, score, candidate in by_frame.get(frame, np.empty((0, 4)))]
        for frame in frames
    ]
    chosen = _viterbi(options, frames, fps, config)

    rows = []
    previous: tuple[int, float, float] | None = None
    for index, frame in enumerate(frames):
        pick = chosen[index]
        if pick is None:
            rows.append({"frame_number": frame, "timestamp_ms": timestamps.get(frame, np.nan),
                         "x": np.nan, "y": np.nan, "score": np.nan, "visible": False,
                         "candidate": -1, "interpolated": False, "speed_px_s": np.nan})
            continue
        x, y, score, candidate = options[index][pick]
        speed = np.nan
        if previous is not None:
            gap_s = (frame - previous[0]) / fps
            if gap_s > 0:
                speed = float(np.hypot(x - previous[1], y - previous[2]) / gap_s)
        rows.append({"frame_number": frame, "timestamp_ms": timestamps.get(frame, np.nan),
                     "x": x, "y": y, "score": score, "visible": True, "candidate": candidate,
                     "interpolated": False, "speed_px_s": speed})
        previous = (frame, x, y)

    track = pd.DataFrame(rows, columns=TRACK_COLUMNS)
    if config.interpolate_max_gap_frames > 0:
        track = interpolate_short_gaps(track, config.interpolate_max_gap_frames)
    return track


def _viterbi(
    options: list[list[tuple[float, float, float, int]]], frames: list[int], fps: float,
    config: BallTrackConfig,
) -> list[int | None]:
    """Lowest-cost sequence of choices; None means the frame is left missing.

    Second order: a state is the pair (previous choice, current choice), so the
    transition to the next frame knows the current velocity and can compare a
    candidate with the constant-velocity prediction.
    """
    if not frames:
        return []
    picks_per_frame = [[None, *range(len(frame_options))] for frame_options in options]

    def position(index: int, pick: int | None) -> tuple[float, float] | None:
        return None if pick is None else options[index][pick][:2]

    def emission(index: int, pick: int | None) -> float:
        return config.miss_cost if pick is None else -options[index][pick][2]

    # States after the first frame: (previous pick, current pick).
    costs: dict[tuple[int | None, int | None], float] = {}
    for pick in picks_per_frame[0]:
        costs[(None, pick)] = emission(0, pick) + (
            0.0 if pick is None else config.restart_cost)
    back: list[dict[tuple[int | None, int | None], tuple[int | None, int | None]]] = [{}]

    for index in range(1, len(frames)):
        gap = frames[index] - frames[index - 1]
        step: dict[tuple[int | None, int | None], float] = {}
        pointers: dict[tuple[int | None, int | None], tuple[int | None, int | None]] = {}
        for pick in picks_per_frame[index]:
            for state, cost in costs.items():
                previous_pick, current_pick = state
                transition = _transition_cost(
                    position(index - 2, previous_pick) if index >= 2 else None,
                    position(index - 1, current_pick), position(index, pick), gap, fps, config,
                )
                if transition == INF:
                    continue
                total = cost + transition + emission(index, pick)
                key = (current_pick, pick)
                if total < step.get(key, INF):
                    step[key] = total
                    pointers[key] = state
        if not step:  # every continuation was impossible; restart from "missing"
            step = {(None, None): min(costs.values()) + config.miss_cost}
            pointers = {(None, None): min(costs, key=lambda k: costs[k])}
        costs = step
        back.append(pointers)

    state = min(costs, key=lambda k: costs[k])
    picks: list[int | None] = [state[1]]
    for index in range(len(frames) - 1, 0, -1):
        state = back[index].get(state, (None, state[0]))
        picks.append(state[1])
    picks.reverse()
    return picks


def _transition_cost(
    before: tuple[float, float] | None, previous: tuple[float, float] | None,
    current: tuple[float, float] | None, gap_frames: int, fps: float, config: BallTrackConfig,
) -> float:
    """Cost of following `previous` with `current`, given the position before that."""
    if current is None:
        return 0.0
    if previous is None:
        return config.restart_cost
    gap_s = max(gap_frames, 1) / fps
    allowed = config.max_speed_px_s * gap_s
    travelled = float(np.hypot(current[0] - previous[0], current[1] - previous[1]))
    if travelled > allowed:
        return INF
    if before is None:
        # No velocity yet: mildly prefer the nearer candidate.
        return config.motion_weight * travelled / allowed
    predicted = (2 * previous[0] - before[0], 2 * previous[1] - before[1])
    deviation = float(np.hypot(current[0] - predicted[0], current[1] - predicted[1]))
    return config.motion_weight * min(deviation / config.prediction_tolerance_px, 1.0)


def interpolate_short_gaps(track: pd.DataFrame, max_gap_frames: int) -> pd.DataFrame:
    """Fill gaps of at most `max_gap_frames` between two visible observations.

    Filled rows are marked `interpolated` and keep a null score: they are a
    convenience for visualisation, not evidence that the ball was seen.
    """
    out = track.copy()
    visible = out.index[out["visible"].to_numpy(dtype=bool)].tolist()
    for start, end in zip(visible, visible[1:], strict=False):
        gap = int(cast(float, out.at[end, "frame_number"])
                  - cast(float, out.at[start, "frame_number"]))
        rows = end - start - 1
        if rows <= 0 or gap > max_gap_frames:
            continue
        for offset in range(1, rows + 1):
            t = offset / (rows + 1)
            out.at[start + offset, "x"] = (1 - t) * out.at[start, "x"] + t * out.at[end, "x"]
            out.at[start + offset, "y"] = (1 - t) * out.at[start, "y"] + t * out.at[end, "y"]
            out.at[start + offset, "interpolated"] = True
    return out


def run_record(
    config: BallTrackConfig, detector_run: dict[str, Any], track: pd.DataFrame,
    candidates: pd.DataFrame,
) -> dict[str, Any]:
    visible = track["visible"].astype(bool)
    scores = track.loc[visible, "score"]
    speeds = track.loc[visible, "speed_px_s"].dropna()
    return {
        "stage": "ball_track",
        "config": asdict(config),
        "detector_run": detector_run,
        "frames": len(track),
        "frames_with_ball": int(visible.sum()),
        "coverage": round(float(visible.mean()), 4) if len(track) else None,
        "interpolated_frames": int(track["interpolated"].astype(bool).sum()),
        "median_score": round(float(scores.median()), 4) if len(scores) else None,
        "candidates_considered": len(candidates[candidates["candidate"] >= 0]),
        "candidates_outside_gate": int((~candidates["in_gate"].astype(bool)).sum())
        if "in_gate" in candidates else None,
        "median_speed_px_s": round(float(speeds.median()), 1) if len(speeds) else None,
        "p95_speed_px_s": round(float(np.percentile(speeds, 95)), 1) if len(speeds) else None,
        "longest_gap_frames": _longest_gap(track),
    }


def _longest_gap(track: pd.DataFrame) -> int:
    visible = track["visible"].to_numpy(dtype=bool)
    longest = current = 0
    for value in visible:
        current = 0 if value else current + 1
        longest = max(longest, current)
    return longest
