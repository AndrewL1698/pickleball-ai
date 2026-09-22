"""Interactive OpenCV tool for labeling ball centres frame by frame.

One frame at a time, with a magnified inset around the cursor because the ball
is only a handful of pixels across in the far court. Every frame is given one of
the three states the evaluation understands: clicked (visible), absent, or
unsure. Progress is written after every frame, so a long labeling session can be
interrupted and resumed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pickleball_ml.evaluation.ball import SCHEMA
from pickleball_ml.video.reader import Frame, iter_frames

WINDOW = "pbml label-ball"
HELP = "click: ball | a: absent | u: unsure | b: back | enter: save+quit | esc: abort"
ZOOM = 6
INSET = 140


def label_ball_frames(
    video: Path, frames: list[int], output: Path, clip: str, window: str, split: str,
    annotator: str,
) -> int:
    """Label the given frames; returns the number labeled. Resumes from `output`."""
    existing = _load_existing(output)
    images = _load_images(video, frames)
    order = [f for f in frames if f in images]
    labels: dict[int, dict[str, Any]] = dict(existing)
    cursor = [0, 0]
    clicked: list[tuple[float, float]] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        cursor[0], cursor[1] = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((float(x), float(y)))

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)
    index = next((i for i, f in enumerate(order) if f not in labels), 0)
    try:
        while index < len(order):
            frame_number = order[index]
            if clicked:
                x, y = clicked.pop()
                labels[frame_number] = {"state": "visible", "xy": [round(x, 1), round(y, 1)]}
                _save(output, labels, video, clip, window, split, annotator)
                index += 1
                continue
            clicked.clear()
            status = (f"{index + 1}/{len(order)}  frame {frame_number}  "
                      f"labeled {len(labels)}")
            cv2.imshow(WINDOW, _render(images[frame_number], status,
                                       (cursor[0], cursor[1]), labels.get(frame_number)))
            key = cv2.waitKey(20) & 0xFF
            if key == 27:
                return len(labels)
            if key in (13, 10):
                break
            if key == ord("a"):
                labels[frame_number] = {"state": "absent", "xy": None}
                _save(output, labels, video, clip, window, split, annotator)
                index += 1
            elif key == ord("u"):
                labels[frame_number] = {"state": "unsure", "xy": None}
                _save(output, labels, video, clip, window, split, annotator)
                index += 1
            elif key == ord("b"):
                index = max(0, index - 1)
    finally:
        cv2.destroyWindow(WINDOW)
    _save(output, labels, video, clip, window, split, annotator)
    return len(labels)


def _load_images(video: Path, frames: list[int]) -> dict[int, Frame]:
    wanted = set(frames)
    images: dict[int, Frame] = {}
    for frame_number, _, image in iter_frames(video, min(frames), max(frames) + 1):
        if frame_number in wanted:
            images[frame_number] = image
    return images


def _load_existing(output: Path) -> dict[int, dict[str, Any]]:
    if not output.exists():
        return {}
    data = json.loads(output.read_text())
    return {int(f["frame_number"]): {"state": f["state"], "xy": f.get("xy")}
            for f in data.get("frames", []) if f.get("state")}


def _save(
    output: Path, labels: dict[int, dict[str, Any]], video: Path, clip: str, window: str,
    split: str, annotator: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema": SCHEMA,
        "clip": clip,
        "window": window,
        "video": str(video),
        "annotator": annotator,
        "created_at": datetime.now(UTC).isoformat(),
        "frames": [{"frame_number": f, "split": split, **labels[f]} for f in sorted(labels)],
    }, indent=2) + "\n")


def _render(
    image: Frame, status: str, cursor: tuple[int, int], existing: dict[str, Any] | None
) -> Frame:
    out = image.copy()
    if existing and existing.get("xy"):
        x, y = existing["xy"]
        cv2.circle(out, (round(x), round(y)), 12, (0, 255, 0), 2)
    _draw_inset(out, cursor)
    cv2.rectangle(out, (0, 0), (out.shape[1], 56), (0, 0, 0), -1)
    cv2.putText(out, status, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                cv2.LINE_AA)
    cv2.putText(out, HELP, (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 220, 255), 1,
                cv2.LINE_AA)
    return out


def _draw_inset(image: Frame, cursor: tuple[int, int]) -> None:
    """Magnified view around the cursor, in the corner away from it."""
    height, width = image.shape[:2]
    half = INSET // (2 * ZOOM)
    x = int(np.clip(cursor[0], half, width - half - 1))
    y = int(np.clip(cursor[1], half, height - half - 1))
    patch = image[y - half:y + half, x - half:x + half]
    if patch.size == 0:
        return
    zoomed = cv2.resize(patch, (INSET * 2, INSET * 2), interpolation=cv2.INTER_NEAREST)
    centre = INSET
    cv2.line(zoomed, (centre, centre - 14), (centre, centre + 14), (0, 0, 255), 1)
    cv2.line(zoomed, (centre - 14, centre), (centre + 14, centre), (0, 0, 255), 1)
    top = height - zoomed.shape[0] if cursor[1] < height // 2 else 0
    left = 0 if cursor[0] > width // 2 else width - zoomed.shape[1]
    image[top:top + zoomed.shape[0], left:left + zoomed.shape[1]] = zoomed
    cv2.rectangle(image, (left, top),
                  (left + zoomed.shape[1] - 1, top + zoomed.shape[0] - 1), (255, 255, 255), 1)
