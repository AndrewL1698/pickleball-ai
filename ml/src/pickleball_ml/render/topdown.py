"""Side-by-side debug video: source frame with detections + animated top-down court."""

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.court.calibration import Calibration, draw_court_lines
from pickleball_ml.court.spec import HALF_LENGTH_FT, HALF_WIDTH_FT, KITCHEN_DEPTH_FT, court_lines
from pickleball_ml.video.reader import Frame, iter_frames

BGR = tuple[int, int, int]

PALETTE: list[BGR] = [
    (75, 25, 230), (75, 180, 60), (25, 225, 255), (200, 130, 0), (48, 130, 245),
    (180, 30, 145), (240, 240, 70), (230, 50, 240), (60, 245, 210), (212, 190, 250),
]


def track_color(track_id: int) -> BGR:
    return PALETTE[track_id % len(PALETTE)]


@dataclass(frozen=True)
class CourtCanvas:
    """Maps court feet to pixels on a top-down canvas, near baseline at the bottom."""

    height_px: int
    side_view_ft: float = 8.0
    end_view_ft: float = 10.0

    @property
    def px_per_ft(self) -> float:
        return self.height_px / (2 * (HALF_LENGTH_FT + self.end_view_ft))

    @property
    def width_px(self) -> int:
        return round(2 * (HALF_WIDTH_FT + self.side_view_ft) * self.px_per_ft)

    def to_px(self, court_x: float, court_y: float) -> tuple[int, int]:
        s = self.px_per_ft
        return (
            round((court_x + HALF_WIDTH_FT + self.side_view_ft) * s),
            round((HALF_LENGTH_FT + self.end_view_ft - court_y) * s),
        )

    def blank(self) -> Frame:
        canvas = np.full((self.height_px, self.width_px, 3), (70, 70, 70), dtype=np.uint8)
        w, h, k = HALF_WIDTH_FT, HALF_LENGTH_FT, KITCHEN_DEPTH_FT
        cv2.rectangle(canvas, self.to_px(-w, h), self.to_px(w, -h), (120, 70, 40), -1)
        cv2.rectangle(canvas, self.to_px(-w, k), self.to_px(w, -k), (170, 110, 60), -1)
        for (x0, y0), (x1, y1) in court_lines().values():
            cv2.line(canvas, self.to_px(x0, y0), self.to_px(x1, y1), (255, 255, 255), 2,
                     cv2.LINE_AA)
        return canvas


def render_topdown_video(
    video: Path,
    output: Path,
    calibration: Calibration,
    raw: pd.DataFrame,
    players: pd.DataFrame,
    start_frame: int,
    end_frame: int | None,
    stride: int,
    fps: float,
    panel_height: int = 720,
    trail_seconds: float = 1.0,
    progress: Callable[[int], None] | None = None,
) -> None:
    canvas = CourtCanvas(panel_height)
    raw_by_frame = {f: g for f, g in raw.groupby("frame_number")}
    players_by_frame = {f: g for f, g in players.groupby("frame_number")}
    trail_len = max(1, round(trail_seconds * fps / stride))
    trails: dict[int, deque[tuple[int, int]]] = defaultdict(lambda: deque(maxlen=trail_len))
    last_seen: dict[int, int] = {}
    writer: cv2.VideoWriter | None = None

    try:
        frames = iter_frames(video, start_frame, end_frame, stride)
        for frame_index, (frame_number, timestamp_ms, image) in enumerate(frames):
            frame_raw = raw_by_frame.get(frame_number)
            frame_players = players_by_frame.get(frame_number)
            left = _video_panel(image, calibration, frame_raw, frame_players)
            scale = panel_height / left.shape[0]
            left = cast(Frame, cv2.resize(left, (round(left.shape[1] * scale), panel_height),
                                          interpolation=cv2.INTER_AREA))
            right = _court_panel(canvas, frame_players, trails, last_seen, frame_index)
            _label(left, f"frame {frame_number}  t={timestamp_ms / 1000:.2f}s")
            composite = np.hstack([left, right])
            if writer is None:
                writer = _open_writer(output, fps / stride, composite.shape[1], composite.shape[0])
            writer.write(composite)
            if progress is not None:
                progress(frame_number)
    finally:
        if writer is not None:
            writer.release()


def _video_panel(
    image: Frame,
    calibration: Calibration,
    frame_raw: pd.DataFrame | None,
    frame_players: pd.DataFrame | None,
) -> Frame:
    """Source frame: court lines, every detection (dark: outside the court gate, light gray:
    gated but not a resolved player), and resolved players colored by identity."""
    out = image.copy()
    draw_court_lines(out, calibration, (0, 0, 255), 1)
    if frame_raw is not None:
        gated = frame_raw["in_gate"].tolist()
        for (x1, y1, x2, y2), in_gate in zip(_int_columns(frame_raw, ["x1", "y1", "x2", "y2"]),
                                             gated, strict=True):
            color = (190, 190, 190) if in_gate else (90, 90, 90)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
    if frame_players is not None:
        rows = zip(
            _int_columns(frame_players,
                         ["player_id", "track_id", "x1", "y1", "x2", "y2", "image_x", "image_y"]),
            frame_players["slot"].tolist(),
            frame_players["truncated"].tolist(),
            frame_players["identity_confident"].tolist(),
            strict=True,
        )
        for (player_id, track_id, x1, y1, x2, y2, foot_x, foot_y), slot, cut, confident in rows:
            color = track_color(player_id)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)
            cv2.circle(out, (foot_x, foot_y), 6, color, -1)
            label = f"P{player_id}{'' if confident else '?'} {slot} t{track_id}"
            if cut:
                label += " (feet cut off)"
            cv2.putText(out, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2,
                        cv2.LINE_AA)
    return out


def _court_panel(
    canvas: CourtCanvas,
    frame_players: pd.DataFrame | None,
    trails: dict[int, deque[tuple[int, int]]],
    last_seen: dict[int, int],
    frame_index: int,
) -> Frame:
    """Top-down court with one dot and trail per resolved player.

    A player missing for less than the trail length keeps their last dot (drawn
    hollow) so brief detection gaps do not flicker.
    """
    panel = canvas.blank()
    if frame_players is not None:
        ids = frame_players["player_id"].astype(int).tolist()
        positions = frame_players[["smooth_x", "smooth_y"]].to_numpy(dtype=float).tolist()
        for player_id, (court_x, court_y) in zip(ids, positions, strict=True):
            trails[player_id].append(canvas.to_px(court_x, court_y))
            last_seen[player_id] = frame_index
    for player_id in sorted(trails):
        missing = frame_index - last_seen[player_id]
        if missing > (trails[player_id].maxlen or 1):
            del trails[player_id]
            continue
        color = track_color(player_id)
        points = list(trails[player_id])
        if len(points) >= 2:
            cv2.polylines(panel, [np.array(points, dtype=np.int32)], False, color, 2, cv2.LINE_AA)
        cv2.circle(panel, points[-1], 8, color, -1 if missing == 0 else 2)
        cv2.circle(panel, points[-1], 9, (0, 0, 0), 1)
        cv2.putText(panel, f"P{player_id}", (points[-1][0] + 11, points[-1][1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return panel


def _int_columns(df: pd.DataFrame, columns: list[str]) -> list[list[int]]:
    values: list[list[int]] = np.rint(df[columns].to_numpy(dtype=float)).astype(int).tolist()
    return values


def _label(image: Frame, text: str) -> None:
    cv2.rectangle(image, (0, 0), (420, 32), (0, 0, 0), -1)
    cv2.putText(image, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1,
                cv2.LINE_AA)


def _open_writer(path: Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    for codec in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*codec), fps, (width, height))
        if writer.isOpened():
            return writer
        writer.release()
    raise OSError(f"could not open a video writer for {path}")
