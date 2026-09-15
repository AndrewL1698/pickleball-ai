"""Person detection with a pretrained YOLO model, court gating, and ByteTrack.

Detections whose ground-contact point falls outside the tracking gate (court
plus margins) are kept in the output but never given to the tracker. Otherwise
ByteTrack's IoU matching can hand a player's track ID to a person on a
neighboring court whose box overlaps the player's box in the image.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import yaml

from pickleball_ml.court.calibration import Calibration
from pickleball_ml.court.geometry import apply_homography
from pickleball_ml.players.appearance import torso_histogram
from pickleball_ml.players.court_position import PlayArea, foot_points
from pickleball_ml.video.reader import iter_frames

PERSON_CLASS = 0
DEFAULT_TRACKER = Path(__file__).with_name("bytetrack.yaml")

RAW_COLUMNS = [
    "frame_number",
    "timestamp_ms",
    "track_id",
    "x1",
    "y1",
    "x2",
    "y2",
    "confidence",
    "court_x",
    "court_y",
    "in_gate",
    "appearance",
]


@dataclass(frozen=True)
class TrackingConfig:
    model: str = "weights/yolo26m.pt"
    imgsz: int = 1280
    conf: float = 0.2
    device: str = "mps"
    tracker: str = str(DEFAULT_TRACKER)
    stride: int = 2
    start_frame: int = 0
    end_frame: int | None = None
    # Generous gate: far-court foot positions can be several feet off when a
    # player lunges or is seen through the net. Identity linking is stricter.
    gate_side_margin_ft: float = 6.0
    gate_near_end_margin_ft: float = 14.0
    gate_far_end_margin_ft: float = 12.0

    @property
    def gate(self) -> PlayArea:
        return PlayArea(self.gate_side_margin_ft, self.gate_near_end_margin_ft,
                        self.gate_far_end_margin_ft)


def track_people(
    video: Path,
    calibration: Calibration,
    config: TrackingConfig,
    progress: Callable[[int, float], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Detect people, gate them by court position, and track the gated ones.

    Returns one row per detection (track_id is -1 for detections outside the
    gate or not confirmed by the tracker) plus a run record describing exactly
    how the output was made.
    """
    from ultralytics import YOLO  # heavy imports; keep module import cheap
    from ultralytics.trackers.byte_tracker import BYTETracker

    model = YOLO(config.model)
    tracker_settings = yaml.safe_load(Path(config.tracker).read_text())
    tracker = BYTETracker(SimpleNamespace(**tracker_settings))
    gate = config.gate
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    processed = 0

    for frame_number, timestamp_ms, image in iter_frames(
        video, config.start_frame, config.end_frame, config.stride
    ):
        result = model.predict(image, classes=[PERSON_CLASS], imgsz=config.imgsz, conf=config.conf,
                               device=config.device, verbose=False)[0]
        boxes = result.boxes.cpu().numpy()
        xyxy = np.asarray(boxes.xyxy, dtype=np.float64).reshape(-1, 4)
        court = apply_homography(calibration.homography, foot_points(xyxy)) if len(xyxy) else (
            np.empty((0, 2))
        )
        in_gate = gate.contains(court[:, 0], court[:, 1])

        track_ids = np.full(len(xyxy), -1, dtype=np.int64)
        gated_index = np.flatnonzero(in_gate)
        tracks = tracker.update(boxes[in_gate], image)
        for track in np.asarray(tracks).reshape(-1, 8):
            track_ids[gated_index[int(track[7])]] = int(track[4])

        for i, (x1, y1, x2, y2) in enumerate(xyxy):
            descriptor = torso_histogram(image, x1, y1, x2, y2) if in_gate[i] else None
            rows.append({
                "frame_number": frame_number,
                "timestamp_ms": timestamp_ms,
                "track_id": int(track_ids[i]),
                "x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2),
                "confidence": float(boxes.conf[i]),
                "court_x": float(court[i, 0]),
                "court_y": float(court[i, 1]),
                "in_gate": bool(in_gate[i]),
                "appearance": None if descriptor is None else descriptor.tolist(),
            })
        processed += 1
        if progress is not None:
            progress(frame_number, processed / max(time.monotonic() - started, 1e-9))

    detections = pd.DataFrame(rows, columns=RAW_COLUMNS)
    run = {
        "stage": "player_detection_tracking",
        "model_name": Path(config.model).name,
        "model_sha256": _sha256(Path(config.model)),
        "ultralytics_version": version("ultralytics"),
        "torch_version": version("torch"),
        "tracker_config": tracker_settings,
        "calibration_homography": calibration.homography.tolist(),
        "config": asdict(config),
        "video": str(video),
        "frames_processed": processed,
        "detections": len(detections),
        "gated_detections": int(detections["in_gate"].sum()) if len(detections) else 0,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "created_at": datetime.now(UTC).isoformat(),
    }
    return detections, run


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
