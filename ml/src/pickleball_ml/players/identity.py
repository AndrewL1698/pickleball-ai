"""Resolve raw tracker IDs into persistent player identities.

Trackers fragment a player into many track IDs (brief missed detections,
occlusion at the net, walking out of frame), occasionally let a track jump to
another person, and can hand an ID from one partner to the other while they
stand overlapping. Identity is resolved in two separate steps per court half:

Who is a player (selection)
    1. Split each raw track into segments wherever the ground position jumps
       faster than a person can move, the box size changes abruptly, or the
       clothing color changes abruptly (the tracker handed the ID to a partner
       standing right next to the player).
    2. Cut segments into tracklets at every moment another segment on the same
       half starts or ends, so overlapping duplicates can be resolved moment by
       moment instead of keeping or dropping a whole segment.
    3. Select at most `players_per_side` simultaneous tracklets with a min-cost
       network flow: covered frames are rewarded, links between tracklets cost
       more the less plausible the motion. Unselected tracklets (people on
       neighboring courts, passers-by, duplicates) are dropped.

Which partner is which (assignment)
    4. Walk through time in intervals with a fixed set of selected tracklets and
       choose which partner each tracklet belongs to, minimizing clothing-color
       mismatch against each partner's color profile, implausible motion, and
       switching a continuous tracker segment to the other partner. Solved
       exactly with Viterbi; partner color profiles are re-estimated from the
       result a few times. The cost gap to the best alternative assignment is
       kept as a per-row identity confidence.

Assumes teams do not switch ends inside the processed window.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, cast

import networkx as nx
import numpy as np
import pandas as pd

from pickleball_ml.players.appearance import Descriptor, appearance_distance, mean_descriptor
from pickleball_ml.players.court_position import PlayArea

COST_SCALE = 100  # network simplex needs integer costs
SIDES = ("near", "far")
INF = float("inf")


@dataclass(frozen=True)
class IdentityConfig:
    players_per_side: int = 2
    smoothing_frames: int = 5
    max_speed_ft_s: float = 20.0
    typical_speed_ft_s: float = 8.0
    near_position_noise_ft: float = 2.0
    far_position_noise_ft: float = 4.0
    box_height_jump_ratio: float = 1.5
    appearance_change_window: int = 15  # rows averaged on each side of a candidate split
    appearance_change_threshold: float = 0.45  # ~99.5th percentile within tracks on test clip
    max_gap_s: float = 30.0
    max_overlap_frames: int = 2
    # Selection: frames inside this area earn full reward; outside it people are rarely players.
    reward_area: PlayArea = field(default_factory=lambda: PlayArea(4.0, 10.0, 5.0))
    outside_reward: float = 0.05
    selection_motion_weight: float = 2.0
    # Assignment costs are in "frames of clothing evidence": a frame whose color is as far
    # from one partner's profile as from the other adds 0 difference.
    assignment_motion_weight: float = 10.0
    motion_cost_cap: float = 5.0
    segment_switch_cost: float = 20.0
    prototype_iterations: int = 5
    confidence_margin: float = 5.0


@dataclass(frozen=True)
class Tracklet:
    tracklet_id: int
    segment_id: int
    track_id: int
    side: str
    start_frame: int
    end_frame: int
    frames: int
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    appearance: Descriptor | None
    reward: float


def split_tracklets(
    detections: pd.DataFrame, source_fps: float, stride: int, config: IdentityConfig
) -> pd.DataFrame:
    """Tracked rows with smoothed court position, `segment_id`, and `tracklet_id` columns."""
    rows = detections[detections["track_id"] >= 0].sort_values(["track_id", "frame_number"])
    rows = rows.reset_index(drop=True)
    if rows.empty:
        return rows.assign(smooth_x=pd.Series(dtype=float), smooth_y=pd.Series(dtype=float),
                           segment_id=pd.Series(dtype=int), tracklet_id=pd.Series(dtype=int))

    by_track = rows.groupby("track_id")
    window = config.smoothing_frames
    for axis in ("x", "y"):
        rows[f"smooth_{axis}"] = by_track[f"court_{axis}"].transform(
            lambda s: s.rolling(window, center=True, min_periods=1).median()
        )
    height = rows["y2"] - rows["y1"]
    dt = by_track["frame_number"].diff() / source_fps
    step = np.hypot(by_track["smooth_x"].diff(), by_track["smooth_y"].diff())
    noise = np.where(rows["smooth_y"] > 0, config.far_position_noise_ft,
                     config.near_position_noise_ft)
    position_jump = step > noise + config.max_speed_ft_s * dt
    ratio = height / height.groupby(rows["track_id"]).shift()
    size_jump = (ratio > config.box_height_jump_ratio) | (ratio < 1 / config.box_height_jump_ratio)
    color_change = _appearance_change_points(rows, config.appearance_change_window,
                                             config.appearance_change_threshold)
    new_segment = dt.isna() | position_jump | size_jump | color_change
    segment = new_segment.astype(int).groupby(rows["track_id"]).cumsum()
    rows["segment_id"] = pd.factorize(pd.MultiIndex.from_arrays([rows["track_id"], segment]))[0]
    rows["tracklet_id"] = _cut_at_side_boundaries(rows, stride)
    return rows


def _appearance_change_points(rows: pd.DataFrame, window: int, threshold: float) -> pd.Series:
    """Rows where a track's mean clothing color over the next `window` rows differs sharply
    from the previous `window` rows (a local maximum above `threshold`)."""
    change = np.zeros(len(rows), dtype=bool)
    for _, group in rows.groupby("track_id", sort=False):
        described = group[group["appearance"].notna()]
        if len(described) < 2 * window:
            continue
        colors = np.stack([np.asarray(a, dtype=np.float64) for a in described["appearance"]])
        cumulative = np.vstack([np.zeros(colors.shape[1]), np.cumsum(colors, axis=0)])
        index = np.arange(window, len(described) - window + 1)
        before = (cumulative[index] - cumulative[index - window]) / window
        after = (cumulative[index + window] - cumulative[index]) / window
        coefficient = np.sqrt(before * after).sum(axis=1)
        distance = np.sqrt(np.clip(1.0 - coefficient, 0.0, 1.0))
        positions = rows.index.get_indexer(described.index)
        for k, i in enumerate(index):
            if distance[k] < threshold:
                continue
            neighborhood = distance[max(0, k - window): k + window + 1]
            if distance[k] >= neighborhood.max():
                change[positions[i]] = True
    return pd.Series(change, index=rows.index)


def _cut_at_side_boundaries(rows: pd.DataFrame, stride: int) -> pd.Series:
    """Tracklet ids: segments cut wherever another segment on the same half starts or ends."""
    by_segment = rows.groupby("segment_id")
    side = np.sign(by_segment["smooth_y"].transform("median")).to_numpy()
    start = by_segment["frame_number"].transform("min").to_numpy()
    end = by_segment["frame_number"].transform("max").to_numpy()
    frames = rows["frame_number"].to_numpy()
    piece = np.zeros(len(rows), dtype=np.int64)
    for half in np.unique(side):
        members = side == half
        boundaries = np.unique(np.concatenate([start[members], end[members] + stride]))
        piece[members] = (np.searchsorted(boundaries, frames[members], side="right")
                          - np.searchsorted(boundaries, start[members], side="right"))
    keys = pd.MultiIndex.from_arrays([rows["segment_id"], piece])
    return pd.Series(pd.factorize(keys)[0], index=rows.index)


def summarize_tracklets(rows: pd.DataFrame, config: IdentityConfig) -> list[Tracklet]:
    tracklets = []
    for _, group in rows.groupby("tracklet_id", sort=True):
        group = group.sort_values("frame_number")
        xs = group["smooth_x"].to_numpy(dtype=float)
        ys = group["smooth_y"].to_numpy(dtype=float)
        ends = min(5, len(group))
        inside = config.reward_area.contains(xs, ys)
        descriptors = [np.asarray(d, dtype=np.float32) for d in group["appearance"]
                       if d is not None]
        tracklets.append(Tracklet(
            tracklet_id=int(group["tracklet_id"].to_numpy()[0]),
            segment_id=int(group["segment_id"].to_numpy()[0]),
            track_id=int(group["track_id"].to_numpy()[0]),
            side="far" if float(np.median(ys)) > 0 else "near",
            start_frame=int(group["frame_number"].iloc[0]),
            end_frame=int(group["frame_number"].iloc[-1]),
            frames=len(group),
            start_xy=(float(np.median(xs[:ends])), float(np.median(ys[:ends]))),
            end_xy=(float(np.median(xs[-ends:])), float(np.median(ys[-ends:]))),
            appearance=mean_descriptor(descriptors),
            reward=float(np.where(inside, 1.0, config.outside_reward).sum()),
        ))
    return tracklets


def motion(
    a: Tracklet, b: Tracklet, source_fps: float, stride: int, config: IdentityConfig
) -> float | None:
    """Normalized displacement from the end of `a` to the start of `b`; None if impossible."""
    gap_frames = b.start_frame - a.end_frame
    if gap_frames <= 0 and -gap_frames > config.max_overlap_frames * stride:
        return None
    gap_s = max(gap_frames, stride) / source_fps
    if gap_s > config.max_gap_s:
        return None
    distance = float(np.hypot(b.start_xy[0] - a.end_xy[0], b.start_xy[1] - a.end_xy[1]))
    noise = config.far_position_noise_ft if a.side == "far" else config.near_position_noise_ft
    if distance > noise + config.max_speed_ft_s * gap_s:
        return None
    return distance / (noise + config.typical_speed_ft_s * gap_s)


def select_players(
    tracklets: list[Tracklet], source_fps: float, stride: int, config: IdentityConfig
) -> list[Tracklet]:
    """Tracklets on one half chosen as players: max coverage minus link cost, at most
    `players_per_side` at a time (min-cost flow over tracklet chains)."""
    k = config.players_per_side
    graph: nx.DiGraph[Any] = nx.DiGraph()
    graph.add_node("S", demand=-k)
    graph.add_node("T", demand=k)
    graph.add_edge("S", "T", capacity=k, weight=0)
    ordered = sorted(tracklets, key=lambda t: t.start_frame)
    for t in ordered:
        graph.add_edge("S", ("in", t.tracklet_id), capacity=1, weight=0)
        graph.add_edge(("in", t.tracklet_id), ("out", t.tracklet_id), capacity=1,
                       weight=-round(t.reward * COST_SCALE))
        graph.add_edge(("out", t.tracklet_id), "T", capacity=1, weight=0)
    starts = np.array([t.start_frame for t in ordered])
    for a in ordered:
        latest_start = a.end_frame + config.max_gap_s * source_fps
        first = int(np.searchsorted(starts, a.start_frame, side="right"))
        last = int(np.searchsorted(starts, latest_start, side="right"))
        for b in ordered[first:last]:
            if a.segment_id == b.segment_id:
                cost = 0.0 if b.start_frame - a.end_frame == stride else None
            else:
                moved = motion(a, b, source_fps, stride, config)
                cost = None if moved is None else config.selection_motion_weight * moved
            if cost is not None:
                graph.add_edge(("out", a.tracklet_id), ("in", b.tracklet_id), capacity=1,
                               weight=round(cost * COST_SCALE))

    flow = nx.min_cost_flow(graph)
    return [t for t in ordered if flow[("in", t.tracklet_id)][("out", t.tracklet_id)] > 0]


@dataclass(frozen=True)
class _Interval:
    """A stretch of time on one half during which the set of selected tracklets is fixed."""

    tracklets: tuple[Tracklet, ...]
    frames: tuple[int, ...]  # rows of each tracklet inside this interval


@dataclass(frozen=True)
class PartnerAssignment:
    label: dict[int, int]  # tracklet_id -> partner index (0 or 1)
    margin: dict[int, float]  # tracklet_id -> cost gap to the best alternative assignment
    prototypes: tuple[Descriptor | None, Descriptor | None]


def assign_partners(
    selected: list[Tracklet], frames_by_tracklet: dict[int, np.ndarray], source_fps: float,
    stride: int, config: IdentityConfig,
) -> PartnerAssignment:
    """Decide which of the two partners each selected tracklet on one half belongs to."""
    intervals = _intervals(selected, frames_by_tracklet, stride)
    prototypes = _initial_prototypes(intervals)
    labels: dict[int, int] = {}
    margins: dict[int, float] = {}
    for _ in range(config.prototype_iterations):
        new_labels, margins = _viterbi(intervals, prototypes, source_fps, stride, config)
        prototypes = _prototypes(selected, new_labels, prototypes)
        if new_labels == labels:
            break
        labels = new_labels
    return PartnerAssignment(labels, margins, (prototypes[0], prototypes[1]))


def _intervals(
    selected: list[Tracklet], frames_by_tracklet: dict[int, np.ndarray], stride: int
) -> list[_Interval]:
    bounds = sorted({t.start_frame for t in selected} | {t.end_frame + stride for t in selected})
    intervals = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        active = []
        for t in selected:
            if t.start_frame < end and t.end_frame >= start:
                frames = frames_by_tracklet[t.tracklet_id]
                count = int(np.searchsorted(frames, end) - np.searchsorted(frames, start))
                active.append((count, t))
        active = sorted(active, key=lambda item: -item[0])[:2]
        intervals.append(_Interval(tuple(t for _, t in active), tuple(c for c, _ in active)))
    return intervals


def _initial_prototypes(intervals: list[_Interval]) -> list[Descriptor | None]:
    """Colors of the two most clearly different tracklets seen at the same time."""
    best: tuple[float, list[Descriptor | None]] = (-1.0, [None, None])
    for interval in intervals:
        if len(interval.tracklets) != 2:
            continue
        a, b = interval.tracklets
        if a.appearance is None or b.appearance is None:
            continue
        score = min(interval.frames) * appearance_distance(a.appearance, b.appearance)
        if score > best[0]:
            best = (score, [a.appearance, b.appearance])
    return best[1]


def _prototypes(
    selected: list[Tracklet], labels: dict[int, int], previous: list[Descriptor | None]
) -> list[Descriptor | None]:
    updated = []
    for label in (0, 1):
        members = [t for t in selected if labels.get(t.tracklet_id) == label
                   and t.appearance is not None]
        if not members:
            updated.append(previous[label])
            continue
        weights = np.array([t.frames for t in members], dtype=np.float64)
        stacked = np.stack([np.asarray(t.appearance, dtype=np.float64) for t in members])
        mean = (stacked * weights[:, None]).sum(axis=0) / weights.sum()
        updated.append((mean / mean.sum()).astype(np.float32))
    return updated


def _states(interval: _Interval) -> list[tuple[int, ...]]:
    """Possible partner labels for the interval's tracklets (two tracklets are two partners)."""
    if len(interval.tracklets) == 2:
        return [(0, 1), (1, 0)]
    if len(interval.tracklets) == 1:
        return [(0,), (1,)]
    return [()]


def _emission(interval: _Interval, state: tuple[int, ...],
              prototypes: list[Descriptor | None]) -> float:
    return sum(frames * appearance_distance(t.appearance, prototypes[label])
               for t, frames, label in zip(interval.tracklets, interval.frames, state, strict=True))


def _transition(
    previous: _Interval, previous_state: tuple[int, ...], current: _Interval,
    state: tuple[int, ...], source_fps: float, stride: int, config: IdentityConfig,
) -> float:
    label_of = {t.tracklet_id: label for t, label in zip(previous.tracklets, previous_state,
                                                           strict=True)}
    by_label = {label: t for t, label in zip(previous.tracklets, previous_state, strict=True)}
    cost = 0.0
    for t, label in zip(current.tracklets, state, strict=True):
        if t.tracklet_id in label_of:
            if label_of[t.tracklet_id] != label:
                return INF  # one tracklet cannot change partner
            continue
        before = by_label.get(label)
        if before is not None and before.segment_id != t.segment_id:
            cost += _assignment_motion_cost(before, t, source_fps, stride, config)
        for other, other_label in zip(previous.tracklets, previous_state, strict=True):
            if other.segment_id == t.segment_id and other_label != label:
                cost += config.segment_switch_cost
    return cost


def _assignment_motion_cost(
    a: Tracklet, b: Tracklet, source_fps: float, stride: int, config: IdentityConfig
) -> float:
    """Cost of the same partner moving from the end of `a` to the start of `b`.

    Capped rather than infinite: selection can start a new chain anywhere (after a tracker
    failure, or from a noisy far-court foot position), and a hard constraint there would make
    both partner choices infeasible.
    """
    gap_s = max(b.start_frame - a.end_frame, stride) / source_fps
    distance = float(np.hypot(b.start_xy[0] - a.end_xy[0], b.start_xy[1] - a.end_xy[1]))
    noise = config.far_position_noise_ft if a.side == "far" else config.near_position_noise_ft
    normalized = distance / (noise + config.typical_speed_ft_s * gap_s)
    return config.assignment_motion_weight * min(normalized, config.motion_cost_cap)


def _viterbi(
    intervals: list[_Interval], prototypes: list[Descriptor | None], source_fps: float,
    stride: int, config: IdentityConfig,
) -> tuple[dict[int, int], dict[int, float]]:
    """Exact best partner labels over all intervals, plus per-tracklet confidence margins."""
    if not intervals:
        return {}, {}
    states = [_states(iv) for iv in intervals]
    emissions = [[_emission(iv, s, prototypes) for s in ss] for iv, ss in
                 zip(intervals, states, strict=True)]
    transitions = [
        [[_transition(intervals[i - 1], p, intervals[i], s, source_fps, stride, config)
          for s in states[i]] for p in states[i - 1]]
        for i in range(1, len(intervals))
    ]
    forward = [list(emissions[0])]
    back: list[list[int]] = [[0] * len(states[0])]
    for i in range(1, len(intervals)):
        row, pointers = [], []
        for j in range(len(states[i])):
            options = [forward[i - 1][p] + transitions[i - 1][p][j]
                       for p in range(len(states[i - 1]))]
            best = int(np.argmin(options))
            row.append(options[best] + emissions[i][j])
            pointers.append(best)
        forward.append(row)
        back.append(pointers)
    backward = [[0.0] * len(ss) for ss in states]
    for i in range(len(intervals) - 2, -1, -1):
        for p in range(len(states[i])):
            backward[i][p] = min(transitions[i][p][j] + emissions[i + 1][j] + backward[i + 1][j]
                                 for j in range(len(states[i + 1])))

    chosen = [int(np.argmin(forward[-1]))]
    for i in range(len(intervals) - 1, 0, -1):
        chosen.append(back[i][chosen[-1]])
    chosen.reverse()

    labels: dict[int, int] = {}
    margins: dict[int, float] = {}
    for i, interval in enumerate(intervals):
        totals = sorted(forward[i][j] + backward[i][j] for j in range(len(states[i])))
        margin = totals[1] - totals[0] if len(totals) > 1 else INF
        for t, label in zip(interval.tracklets, states[i][chosen[i]], strict=True):
            labels.setdefault(t.tracklet_id, label)
            margins[t.tracklet_id] = min(margins.get(t.tracklet_id, INF), margin)
    return labels, margins


def resolve_identities(
    detections: pd.DataFrame, source_fps: float, stride: int, frame_height: int,
    config: IdentityConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per-frame rows for the resolved players, and per-side diagnostics."""
    columns = ["frame_number", "timestamp_ms", "player_id", "team", "slot", "track_id",
               "segment_id", "tracklet_id", "x1", "y1", "x2", "y2", "confidence",
               "image_x", "image_y", "court_x", "court_y", "smooth_x", "smooth_y", "truncated",
               "identity_margin", "identity_confident"]
    rows = split_tracklets(detections, source_fps, stride, config)
    tracklets = summarize_tracklets(rows, config)
    frames_by_tracklet = {cast(int, k): np.sort(g.to_numpy()) for k, g in
                          rows.groupby("tracklet_id")["frame_number"]}
    player_of: dict[int, int] = {}
    margin_of: dict[int, float] = {}
    diagnostics: dict[str, Any] = {"tracklets": len(tracklets)}
    for side_index, side in enumerate(SIDES):
        side_tracklets = [t for t in tracklets if t.side == side]
        selected = select_players(side_tracklets, source_fps, stride, config)
        assignment = assign_partners(selected, frames_by_tracklet, source_fps, stride, config)
        first_seen = {label: min((t.start_frame for t in selected
                                  if assignment.label.get(t.tracklet_id) == label), default=0)
                      for label in (0, 1)}
        order = sorted((0, 1), key=lambda label: first_seen[label])
        base = 1 + side_index * config.players_per_side
        for t in selected:
            label = assignment.label.get(t.tracklet_id)
            if label is None:
                continue
            player_of[t.tracklet_id] = base + order.index(label)
            margin_of[t.tracklet_id] = assignment.margin[t.tracklet_id]
        a, b = assignment.prototypes
        diagnostics[side] = {
            "tracklets": len(side_tracklets),
            "selected": len(selected),
            "rejected_frames": int(sum(t.frames for t in side_tracklets) -
                                   sum(t.frames for t in selected)),
            "partner_color_distance": round(appearance_distance(a, b), 3),
        }

    players = rows[rows["tracklet_id"].isin(list(player_of))].copy()
    if players.empty:
        return pd.DataFrame(columns=columns), diagnostics
    players["player_id"] = players["tracklet_id"].map(player_of)
    players["team"] = np.where(players["player_id"] <= config.players_per_side, "near", "far")
    players["identity_margin"] = players["tracklet_id"].map(margin_of)
    players["identity_confident"] = players["identity_margin"] >= config.confidence_margin
    players = (players.sort_values(["player_id", "frame_number", "tracklet_id"])
               .drop_duplicates(["player_id", "frame_number"], keep="first"))
    players["image_x"] = (players["x1"] + players["x2"]) / 2.0
    players["image_y"] = players["y2"]
    players["truncated"] = players["y2"] >= frame_height - 2
    players["slot"] = _assign_slots(players)
    return players[columns].sort_values(["frame_number", "player_id"]).reset_index(drop=True), \
        diagnostics


def _assign_slots(players: pd.DataFrame) -> pd.Series:
    """Left/right within each half: the lower court_x of two partners is left.

    A lone player on a half is assigned by which side of the center line they stand.
    """
    group = players.groupby(["frame_number", "team"])["smooth_x"]
    count = group.transform("count")
    rank = group.rank(method="first")
    is_left = np.where(count >= 2, rank == 1, players["smooth_x"] < 0)
    return players["team"] + np.where(is_left, "_left", "_right")


def run_record(
    config: IdentityConfig, players: pd.DataFrame, diagnostics: dict[str, Any],
    frames_processed: int,
) -> dict[str, Any]:
    per_frame = players.groupby("frame_number").size()
    coverage = players["player_id"].value_counts().sort_index() / max(frames_processed, 1)
    confident = players["identity_confident"].astype(bool)
    return {
        "stage": "player_identity",
        "config": asdict(config),
        "frames": frames_processed,
        "sides": {side: diagnostics.get(side) for side in SIDES},
        "tracklets": diagnostics.get("tracklets"),
        "frames_with_4_players": int((per_frame == 4).sum()),
        "frames_with_4_players_rate": round(float((per_frame == 4).sum()) / frames_processed, 4)
        if frames_processed else None,
        "coverage_by_player": {str(k): round(float(v), 4) for k, v in coverage.items()},
        "rows": len(players),
        "low_confidence_identity_rows": int((~confident).sum()),
        "low_confidence_identity_rate": round(float((~confident).mean()), 4) if len(players)
        else None,
    }
