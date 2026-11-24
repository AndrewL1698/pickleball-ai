"""Detect: person detection, court gate and ByteTrack over one steady part of a video.

The first half of player tracking (ADR 0001). It stores image-space ground-contact
points, never court coordinates: Identify derives those from the current
calibration. The calibration is used here only for the gate, which keeps people
on neighbouring courts away from ByteTrack.

The work is cut into chunks of `chunk_seconds` of video, measured from the start
of the steady part, so a long match can be checkpointed: each chunk is handed to
the caller as soon as it is finished, and a later pass can start at any chunk.
The tracker starts afresh at every chunk boundary, which is what makes a pass
that starts at chunk k produce exactly the chunks a full pass produces from k on.
Track ids are offset by chunk so they stay unique; Identify already handles
tracks that break up.

The detector and tracker are behind small protocols, so tests use fakes and the
real YOLO model never runs in them. `load_yolo_backend` builds the real ones,
importing ultralytics (and so torch) only when called.
"""

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
import yaml
from numpy.typing import NDArray

from pickleball_ml.court.geometry import FloatArray, apply_homography
from pickleball_ml.players.appearance import torso_histogram
from pickleball_ml.players.court_position import PlayArea, foot_points, truncated
from pickleball_ml.players.tracking import DEFAULT_TRACKER, PERSON_CLASS, file_sha256
from pickleball_ml.video.reader import Frame, iter_frames_at_rate
from pickleball_ml.video.steady_part import SteadyPart

IntArray = NDArray[np.int64]

#: Track ids from chunk k are k * TRACK_ID_STRIDE + the tracker's own id.
TRACK_ID_STRIDE = 1_000_000

DETECT_COLUMNS = [
    "frame_number",
    "timestamp_ms",
    "track_id",
    "x1",
    "y1",
    "x2",
    "y2",
    "confidence",
    "image_x",
    "image_y",
    "truncated",
    "in_gate",
    "appearance",
]


class Detector(Protocol):
    """Finds people in one frame."""

    def detect(self, image: Frame) -> tuple[FloatArray, FloatArray]:
        """Boxes as (N, 4) x1, y1, x2, y2 in image pixels, and their (N,) confidences."""
        ...


class Tracker(Protocol):
    """Links boxes across consecutive frames. One instance per chunk."""

    def update(self, xyxy: FloatArray, confidence: FloatArray, image: Frame) -> IntArray:
        """A track id (> 0) for each box, or -1 where the tracker has none."""
        ...


def _default_tracker_settings() -> dict[str, Any]:
    settings: dict[str, Any] = yaml.safe_load(DEFAULT_TRACKER.read_text())
    return settings


@dataclass(frozen=True)
class DetectConfig:
    """Everything that decides a Detect run's output, apart from the model and video.

    `tracker` is ByteTrack's settings. Its `track_buffer` counts processed frames,
    so unless given explicitly it is set from `track_buffer_seconds` and the rate.
    """

    imgsz: int = 1280
    conf: float = 0.2
    rate_hz: float = 15.0
    chunk_seconds: float = 300.0
    # The same generous gate as Phase 0: far-court feet can be several feet off
    # when a player lunges or is seen through the net. Identify is stricter.
    gate_side_margin_ft: float = 6.0
    gate_near_end_margin_ft: float = 14.0
    gate_far_end_margin_ft: float = 12.0
    track_buffer_seconds: float = 2.0
    tracker: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tracker:
            settings = _default_tracker_settings()
            settings["track_buffer"] = round(self.track_buffer_seconds * self.rate_hz)
            object.__setattr__(self, "tracker", settings)

    @property
    def gate(self) -> PlayArea:
        return PlayArea(
            self.gate_side_margin_ft, self.gate_near_end_margin_ft, self.gate_far_end_margin_ft
        )

    def to_config(self) -> dict[str, Any]:
        config = asdict(self)
        config["tracker"] = dict(self.tracker)
        return config

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "DetectConfig":
        return cls(**config)


@dataclass(frozen=True)
class DetectChunk:
    """One finished chunk: its place in the run, the video it covers, and its rows.

    `end_seconds` is the chunk's nominal end, cut short by the end of the steady
    part, or by the last frame when the video itself ends first.
    """

    index: int
    start_seconds: float
    end_seconds: float
    detections: pd.DataFrame
    is_last: bool


def chunk_start(steady: SteadyPart, config: DetectConfig, index: int) -> float:
    return steady.start_seconds + index * config.chunk_seconds


def detect_chunks(
    video: Path,
    *,
    homography: FloatArray,
    config: DetectConfig,
    steady: SteadyPart,
    detector: Detector,
    new_tracker: Callable[[], Tracker],
    first_chunk: int = 0,
    progress: Callable[[float], None] | None = None,
) -> Iterator[DetectChunk]:
    """Detect over `steady`, from chunk `first_chunk` on, yielding each chunk as it finishes.

    The video is decoded once, sequentially, from its start: frames before the
    first chunk are skipped without being converted. The final chunk has
    `is_last`, and there is always one, even if no frame was left to process.
    `progress` is called with the video time reached, in seconds.
    """
    homography = np.asarray(homography, dtype=np.float64)
    start = chunk_start(steady, config, first_chunk)
    frames = iter_frames_at_rate(video, config.rate_hz, start, steady.end_seconds)

    index = first_chunk
    tracker = new_tracker()
    rows: list[dict[str, Any]] = []
    last_seconds: float | None = None
    for frame_number, timestamp_ms, image in frames:
        seconds = timestamp_ms / 1000.0
        frame_chunk = first_chunk + math.floor(
            (seconds - start) / config.chunk_seconds
        )
        while index < frame_chunk:
            # Also yields an empty chunk for any span of video with no frames in it.
            yield _chunk(steady, config, index, rows, is_last=False)
            index, rows, tracker = index + 1, [], new_tracker()
        rows.extend(
            _detect_frame(
                frame_number, timestamp_ms, image, homography, config, detector, tracker, index
            )
        )
        last_seconds = seconds
        if progress is not None:
            progress(seconds)
    yield _chunk(steady, config, index, rows, is_last=True, last_seconds=last_seconds)


def _chunk(
    steady: SteadyPart,
    config: DetectConfig,
    index: int,
    rows: list[dict[str, Any]],
    *,
    is_last: bool,
    last_seconds: float | None = None,
) -> DetectChunk:
    start = chunk_start(steady, config, index)
    end = start + config.chunk_seconds
    if steady.end_seconds is not None:
        end = min(end, steady.end_seconds)
    if is_last and steady.end_seconds is None:
        # The video ran out: the chunk ends with its last frame.
        end = max(start, last_seconds) if last_seconds is not None else start
    return DetectChunk(
        index=index,
        start_seconds=start,
        end_seconds=end,
        detections=pd.DataFrame(rows, columns=DETECT_COLUMNS),
        is_last=is_last,
    )


def _detect_frame(
    frame_number: int,
    timestamp_ms: float,
    image: Frame,
    homography: FloatArray,
    config: DetectConfig,
    detector: Detector,
    tracker: Tracker,
    chunk_index: int,
) -> list[dict[str, Any]]:
    xyxy, confidence = detector.detect(image)
    xyxy = np.asarray(xyxy, dtype=np.float64).reshape(-1, 4)
    confidence = np.asarray(confidence, dtype=np.float64).reshape(-1)
    feet = foot_points(xyxy)
    court = apply_homography(homography, feet) if len(xyxy) else np.empty((0, 2))
    in_gate = config.gate.contains(court[:, 0], court[:, 1])
    cut_off = truncated(xyxy[:, 3], image.shape[0])

    track_ids = np.full(len(xyxy), -1, dtype=np.int64)
    gated = np.flatnonzero(in_gate)
    local = np.asarray(tracker.update(xyxy[gated], confidence[gated], image), dtype=np.int64)
    track_ids[gated] = np.where(local > 0, chunk_index * TRACK_ID_STRIDE + local, -1)

    rows = []
    for i, (x1, y1, x2, y2) in enumerate(xyxy):
        descriptor = torso_histogram(image, x1, y1, x2, y2) if in_gate[i] else None
        rows.append({
            "frame_number": frame_number,
            "timestamp_ms": timestamp_ms,
            "track_id": int(track_ids[i]),
            "x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2),
            "confidence": float(confidence[i]),
            "image_x": float(feet[i, 0]),
            "image_y": float(feet[i, 1]),
            "truncated": bool(cut_off[i]),
            "in_gate": bool(in_gate[i]),
            "appearance": None if descriptor is None else descriptor.tolist(),
        })
    return rows


# --- The real detector and tracker --------------------------------------------


@dataclass(frozen=True)
class DetectBackend:
    """A detector, a way to make trackers, and the version that names the model."""

    model_version: str
    detector: Detector
    new_tracker: Callable[[], Tracker]


class TrackingUnavailable(RuntimeError):
    """This install cannot run Detect: no ultralytics/torch, or no model file."""


def model_version(model_path: Path) -> str:
    """The model file's name and SHA-256, e.g. `yolo26m.pt@sha256:…`."""
    digest = file_sha256(model_path)
    if digest is None:
        raise TrackingUnavailable(f"no model file at {model_path}")
    return f"{model_path.name}@sha256:{digest}"


def load_yolo_backend(
    model_path: Path, config: DetectConfig, device: str | None = None
) -> DetectBackend:
    """YOLO and ByteTrack, on `device` (default: mps, then cuda, then cpu)."""
    version = model_version(model_path)
    try:
        import torch
        from ultralytics import YOLO
        from ultralytics.trackers.byte_tracker import BYTETracker
    except ImportError as exc:
        raise TrackingUnavailable("ultralytics and torch are not installed") from exc

    if device is None:
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
    model = YOLO(str(model_path))
    tracker_settings = dict(config.tracker)

    class YoloDetector:
        def detect(self, image: Frame) -> tuple[FloatArray, FloatArray]:
            result = model.predict(image, classes=[PERSON_CLASS], imgsz=config.imgsz,
                                   conf=config.conf, device=device, verbose=False)[0]
            boxes = result.boxes.cpu().numpy()
            return (
                np.asarray(boxes.xyxy, dtype=np.float64).reshape(-1, 4),
                np.asarray(boxes.conf, dtype=np.float64).reshape(-1),
            )

    def new_tracker() -> Tracker:
        from types import SimpleNamespace

        return ByteTracker(BYTETracker(SimpleNamespace(**tracker_settings)))

    return DetectBackend(model_version=version, detector=YoloDetector(), new_tracker=new_tracker)


class _Boxes:
    """The slice of ultralytics' `Boxes` that BYTETracker reads."""

    def __init__(self, xyxy: FloatArray, conf: FloatArray) -> None:
        self.xyxy = xyxy
        self.conf = conf
        self.cls = np.zeros(len(conf), dtype=np.float64)
        centre = (xyxy[:, :2] + xyxy[:, 2:]) / 2.0
        self.xywh = np.column_stack([centre, xyxy[:, 2:] - xyxy[:, :2]])

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, mask: Any) -> "_Boxes":
        return _Boxes(self.xyxy[mask].reshape(-1, 4), self.conf[mask].reshape(-1))


class ByteTracker:
    """ultralytics' BYTETracker behind the `Tracker` protocol."""

    def __init__(self, tracker: Any) -> None:
        self._tracker = tracker

    def update(self, xyxy: FloatArray, confidence: FloatArray, image: Frame) -> IntArray:
        xyxy = np.asarray(xyxy, dtype=np.float64).reshape(-1, 4)
        confidence = np.asarray(confidence, dtype=np.float64).reshape(-1)
        ids = np.full(len(xyxy), -1, dtype=np.int64)
        tracks = self._tracker.update(_Boxes(xyxy, confidence), image)
        # Each row is x1, y1, x2, y2, track id, score, class, index of the box.
        for track in np.asarray(tracks).reshape(-1, 8):
            ids[int(track[7])] = int(track[4])
        return ids
