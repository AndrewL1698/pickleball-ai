"""Export sampled frames for manual labeling, and a template to fill in.

Player labels
    Each sampled frame is written as an image with every gated detection drawn
    and numbered, plus a strip of enlarged crops of those boxes so clothing is
    readable even for far-court players. The template lists the same numbered
    candidates. The annotator writes, for each visible player, either the
    candidate number or an explicit box, and lists visible players the detector
    missed with a null box. Predicted identities are deliberately not shown, so
    labeling cannot be anchored to them.

Ball labels
    Each sampled frame is written as an image, optionally with a zoom tile grid,
    and the template lists the frames with an empty state for the annotator.

Templates are written next to the images; the filled file belongs in
`data/annotations/<clip>/` where it is small, hand-authored, and tracked.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.evaluation.ball import SCHEMA as BALL_SCHEMA
from pickleball_ml.evaluation.players import SCHEMA as PLAYER_SCHEMA
from pickleball_ml.video.reader import Frame, iter_frames

CROP_HEIGHT = 200
LABEL_COLOR = (0, 255, 255)


def sample_frames(processed: list[int], every_frames: int) -> list[int]:
    """Every `every_frames`-th processed frame (so labels line up with predictions)."""
    if every_frames < 1:
        raise ValueError("every_frames must be >= 1")
    ordered = sorted(processed)
    if not ordered:
        return []
    chosen = []
    next_frame = ordered[0]
    for frame in ordered:
        if frame >= next_frame:
            chosen.append(frame)
            next_frame = frame + every_frames
    return chosen


def export_player_frames(
    video: Path, detections: pd.DataFrame, frames: list[int], out_dir: Path,
    clip: str, window: str, split: str, gated_only: bool = True,
) -> dict[str, Any]:
    """Write one annotated image per sampled frame and a template label file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = detections[detections["in_gate"]] if gated_only else detections
    by_frame = {cast(int, f): g for f, g in rows.groupby("frame_number")}
    wanted = set(frames)
    template_frames = []
    for frame_number, _, image in iter_frames(video, min(frames), max(frames) + 1):
        if frame_number not in wanted:
            continue
        boxes = by_frame.get(frame_number)
        candidates = [] if boxes is None else [
            [float(v) for v in box]
            for box in boxes[["x1", "y1", "x2", "y2"]].to_numpy(dtype=float)
        ]
        sheet = _player_sheet(image, candidates)
        cv2.imwrite(str(out_dir / f"frame_{frame_number:07d}.jpg"), sheet,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        template_frames.append({
            "frame_number": frame_number,
            "split": split,
            "candidates": [{"id": i, "box": [round(v, 1) for v in box]}
                           for i, box in enumerate(candidates)],
            "players": [],
        })
    template = {
        "schema": PLAYER_SCHEMA,
        "clip": clip,
        "window": window,
        "video": str(video),
        "annotator": "",
        "created_at": datetime.now(UTC).isoformat(),
        "people": {},
        "instructions": (
            "For every court player visible in the frame add "
            "{'person': <name>, 'candidate': <id>}; if the player is visible but has no "
            "candidate box, add {'person': <name>, 'box': null}. Leave players that are not "
            "visible out. Name the four people in 'people' by clothing, not by predicted ID."
        ),
        "frames": template_frames,
    }
    path = out_dir / "player_labels_template.json"
    path.write_text(json.dumps(template, indent=2) + "\n")
    return {"frames": len(template_frames), "template": str(path), "images": str(out_dir)}


def _player_sheet(image: Frame, boxes: list[list[float]]) -> Frame:
    """Frame with numbered boxes, plus a strip of enlarged crops of each box."""
    annotated = image.copy()
    crops = []
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        p1, p2 = (round(x1), round(y1)), (round(x2), round(y2))
        cv2.rectangle(annotated, p1, p2, LABEL_COLOR, 2)
        cv2.putText(annotated, str(index), (p1[0], max(18, p1[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, LABEL_COLOR, 2, cv2.LINE_AA)
        crops.append(_numbered_crop(image, index, x1, y1, x2, y2))
    if not crops:
        return annotated
    strip = np.hstack(crops)
    if strip.shape[1] < annotated.shape[1]:
        pad = np.zeros((strip.shape[0], annotated.shape[1] - strip.shape[1], 3), dtype=np.uint8)
        strip = np.hstack([strip, pad])
    else:
        scale = annotated.shape[1] / strip.shape[1]
        strip = np.asarray(cv2.resize(strip, (annotated.shape[1],
                                              max(1, round(strip.shape[0] * scale)))))
    return cast(Frame, np.vstack([annotated, strip.astype(np.uint8)]))


def _numbered_crop(
    image: Frame, index: int, x1: float, y1: float, x2: float, y2: float
) -> Frame:
    height, width = image.shape[:2]
    left, right = max(0, round(x1) - 4), min(width, round(x2) + 4)
    top, bottom = max(0, round(y1) - 4), min(height, round(y2) + 4)
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        crop = np.zeros((10, 10, 3), dtype=np.uint8)
    scale = CROP_HEIGHT / crop.shape[0]
    resized = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), CROP_HEIGHT))
    cv2.putText(resized, str(index), (4, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, LABEL_COLOR, 2,
                cv2.LINE_AA)
    return np.asarray(cv2.copyMakeBorder(resized, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(0, 0, 0)))


def export_ball_frames(
    video: Path, frames: list[int], out_dir: Path, clip: str, window: str, split: str,
) -> dict[str, Any]:
    """Write one image per sampled frame and a template ball label file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(frames)
    written = []
    for frame_number, _, image in iter_frames(video, min(frames), max(frames) + 1):
        if frame_number not in wanted:
            continue
        cv2.imwrite(str(out_dir / f"frame_{frame_number:07d}.jpg"), image,
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        written.append(frame_number)
    template = {
        "schema": BALL_SCHEMA,
        "clip": clip,
        "window": window,
        "video": str(video),
        "annotator": "",
        "created_at": datetime.now(UTC).isoformat(),
        "instructions": (
            "state: 'visible' with 'xy' at the ball centre in source pixels, 'absent' when the "
            "ball is genuinely not visible, 'unsure' when it cannot be located confidently."
        ),
        "frames": [{"frame_number": f, "split": split, "state": "", "xy": None} for f in written],
    }
    path = out_dir / "ball_labels_template.json"
    path.write_text(json.dumps(template, indent=2) + "\n")
    return {"frames": len(written), "template": str(path), "images": str(out_dir)}
