"""Interactive OpenCV tool for clicking court landmarks on a video frame."""

from typing import cast

import cv2
import numpy as np

from pickleball_ml.court.spec import LANDMARKS
from pickleball_ml.video.reader import Frame

WINDOW = "pbml calibrate"
HELP = "click: place | s: skip | u: undo | enter: finish | esc: abort"


def collect_landmarks(image: Frame) -> dict[str, tuple[float, float]] | None:
    """Prompt for each landmark in turn. Returns None if the user aborts.

    A magnified inset follows the cursor so painted-line intersections can be
    placed precisely. Skip landmarks that are hidden or out of frame.
    """
    names = list(LANDMARKS)
    points: dict[str, tuple[float, float]] = {}
    skipped: list[str] = []
    cursor = [0, 0]
    clicked: list[tuple[float, float]] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        cursor[0], cursor[1] = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((float(x), float(y)))

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)
    try:
        while True:
            index = len(points) + len(skipped)
            if clicked and index < len(names):
                points[names[index]] = clicked.pop()
                continue
            clicked.clear()
            prompt = names[index] if index < len(names) else "all landmarks visited: press enter"
            cv2.imshow(WINDOW, _render(image, points, prompt, (cursor[0], cursor[1])))
            key = cv2.waitKey(20) & 0xFF
            if key == 27:
                return None
            if key in (13, 10) and len(points) >= 4:
                return points
            if key == ord("s") and index < len(names):
                skipped.append(names[index])
            if key == ord("u"):
                _undo(names, points, skipped)
    finally:
        cv2.destroyWindow(WINDOW)


def _undo(names: list[str], points: dict[str, tuple[float, float]], skipped: list[str]) -> None:
    visited = [n for n in names if n in points or n in skipped]
    if not visited:
        return
    last = visited[-1]
    if last in points:
        del points[last]
    else:
        skipped.remove(last)


def _render(
    image: Frame,
    points: dict[str, tuple[float, float]],
    prompt: str,
    cursor: tuple[int, int],
) -> Frame:
    out = image.copy()
    for name, (x, y) in points.items():
        cv2.drawMarker(out, (round(x), round(y)), (0, 255, 255), cv2.MARKER_CROSS, 16, 2)
        cv2.putText(out, name, (round(x) + 8, round(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255), 1, cv2.LINE_AA)
    cv2.rectangle(out, (0, 0), (out.shape[1], 64), (0, 0, 0), -1)
    cv2.putText(out, f"Click: {prompt}", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255),
                2, cv2.LINE_AA)
    cv2.putText(out, HELP, (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
    _draw_magnifier(out, image, cursor)
    return out


def _draw_magnifier(canvas: Frame, image: Frame, cursor: tuple[int, int], radius: int = 24,
                    scale: int = 6) -> None:
    height, width = image.shape[:2]
    x, y = cursor
    padded = cv2.copyMakeBorder(image, radius, radius, radius, radius, cv2.BORDER_CONSTANT)
    patch = padded[y : y + 2 * radius + 1, x : x + 2 * radius + 1]
    if patch.shape[:2] != (2 * radius + 1, 2 * radius + 1):
        return
    zoom = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    center = zoom.shape[0] // 2
    cv2.drawMarker(zoom, (center, center), (0, 0, 255), cv2.MARKER_CROSS, 2 * scale, 1)
    size = zoom.shape[0]
    top = 72
    left = width - size - 8 if x < width // 2 else 8
    if top + size <= height:
        canvas[top : top + size, left : left + size] = zoom
        cv2.rectangle(canvas, (left, top), (left + size, top + size), (255, 255, 255), 1)


def median_background(frames: list[Frame]) -> Frame:
    """Per-pixel median of frames: removes moving players so court lines are unobstructed."""
    return cast(Frame, np.median(np.stack(frames), axis=0).astype(np.uint8))
