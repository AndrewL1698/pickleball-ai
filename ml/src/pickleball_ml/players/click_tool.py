"""Interactive OpenCV tool for labeling players' ground-contact points.

One sampled frame at a time: click where each visible court player's feet meet the
court, then finish the frame. A finished frame is complete, so every court player
visible in it must have a point (see `pickleball_ml.evaluation.ground_contact`).
Predicted boxes are not drawn, so the labels are not anchored on the tracker.

The label file holds one clip; frames from other windows already in it are kept.
Progress is written after every finished frame, so a session can be interrupted
and resumed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2

from pickleball_ml.evaluation.ground_contact import SCHEMA
from pickleball_ml.video.labeling import draw_zoom_inset, load_frames
from pickleball_ml.video.reader import Frame

WINDOW = "pbml label-ground-contact"
HELP = ("click: feet | u: last point unsure | z: undo | n/space: frame done | s: skip frame | "
        "b: back | enter: save+quit | esc: abort")
ZOOM = 3
INSET = 150


def label_ground_contacts(
    video: Path, frames: list[int], output: Path, clip: str, window: str, split: str,
    annotator: str,
) -> int:
    """Label the given frames; returns how many of them are labeled or skipped.

    Resumes from `output`, starting at the first of `frames` it doesn't hold yet.
    """
    header, labels = _load_existing(output)
    images = load_frames(video, frames)
    order = [f for f in frames if f in images]
    cursor = [0, 0]
    clicked: list[tuple[float, float]] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        cursor[0], cursor[1] = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((float(x), float(y)))

    def save() -> None:
        _save(output, header, labels, video, clip, annotator)

    def finish(frame_number: int, entry: dict[str, Any]) -> None:
        labels[frame_number] = {"window": window, "split": split, **entry}
        save()

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)
    index = next((i for i, f in enumerate(order) if f not in labels), 0)
    points = _points_of(labels.get(order[index])) if order else []
    try:
        while index < len(order):
            frame_number = order[index]
            while clicked:
                x, y = clicked.pop(0)
                points.append({"xy": [round(x, 1), round(y, 1)]})
            status = (f"{index + 1}/{len(order)}  frame {frame_number}  "
                      f"points {len(points)}  done {sum(f in labels for f in order)}")
            if labels.get(frame_number, {}).get("skip"):
                status += "  (skipped)"
            cv2.imshow(WINDOW, _render(images[frame_number], status, (cursor[0], cursor[1]),
                                       points))
            key = cv2.waitKey(20) & 0xFF
            if key == 27:
                return sum(f in labels for f in order)
            if key in (13, 10):
                break
            move = 0
            if key in (ord("n"), ord(" ")):
                finish(frame_number, {"points": points})
                move = 1
            elif key == ord("s"):
                finish(frame_number, {"skip": True})
                move = 1
            elif key == ord("b"):
                move = -1
            elif key == ord("z") and points:
                points.pop()
            elif key == ord("u") and points:
                if points[-1].get("unsure"):
                    del points[-1]["unsure"]
                else:
                    points[-1]["unsure"] = True
            if move:
                # Unfinished clicks on the frame being left are dropped.
                index = max(0, index + move)
                if index < len(order):
                    points = _points_of(labels.get(order[index]))
    finally:
        cv2.destroyWindow(WINDOW)
    save()
    return sum(f in labels for f in order)


def _points_of(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(point) for point in (entry or {}).get("points", [])]


def _load_existing(output: Path) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Header fields (kept on save) and every labeled frame already in the file."""
    if not output.exists():
        return {}, {}
    data = json.loads(output.read_text())
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{output}: expected schema {SCHEMA!r}, got {data.get('schema')!r}")
    labels = {int(f["frame_number"]): {k: v for k, v in f.items() if k != "frame_number"}
              for f in data.get("frames", [])}
    header = {k: v for k, v in data.items() if k != "frames"}
    return header, labels


def _save(
    output: Path, header: dict[str, Any], labels: dict[int, dict[str, Any]], video: Path,
    clip: str, annotator: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        **header,
        "schema": SCHEMA,
        "clip": clip,
        "video": str(video),
        "annotator": annotator or header.get("annotator", ""),
        "created_at": header.get("created_at", now),
        "updated_at": now,
        "frames": [{"frame_number": f, **labels[f]} for f in sorted(labels)],
    }, indent=2) + "\n")


def _render(
    image: Frame, status: str, cursor: tuple[int, int], points: list[dict[str, Any]]
) -> Frame:
    out = image.copy()
    for number, point in enumerate(points, start=1):
        x, y = (round(v) for v in point["xy"])
        colour = (0, 165, 255) if point.get("unsure") else (0, 255, 0)
        cv2.circle(out, (x, y), 6, colour, 2)
        label = f"{number}?" if point.get("unsure") else str(number)
        cv2.putText(out, label, (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2,
                    cv2.LINE_AA)
    draw_zoom_inset(out, cursor, ZOOM, INSET)
    cv2.rectangle(out, (0, 0), (out.shape[1], 56), (0, 0, 0), -1)
    cv2.putText(out, status, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                cv2.LINE_AA)
    cv2.putText(out, HELP, (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 220, 255), 1,
                cv2.LINE_AA)
    return out
