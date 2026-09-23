"""Reproducible multi-clip, multi-window experiments.

A manifest (JSON, hand-authored and tracked in git) names the source clips, the
stationary camera segments of each clip, and the windows to process. Running it
writes one directory per window under
`data/processed/<clip_id>/<window_id>/`, with the same file names Phase 0a
writes for a whole video, so nothing overwrites anything else and the Phase 0a
commands keep working unchanged.

Every window directory gets a `window_run.json` recording the clip, the exact
frame range, the source metadata, the calibration used, every stage config,
model names and versions, runtimes, artifact paths, and stage status.

Stages are skipped when their output already exists and the fingerprint of
their inputs and configuration is unchanged, so an interrupted experiment can
be resumed and only what actually changed is recomputed.

Clip-level stages (once per clip or camera segment):

    metadata   video properties
    camera     stability over time, placement, court visibility, sharpness
    calibrate  homography from clicked//measured landmarks + overlay image

Window-level stages:

    track       person detection, court gating, ByteTrack
    identify    track fragments -> four persistent players
    render      side-by-side top-down debug video
    ball        ball detection + conservative filtering
    ball_render ball debug video
    evaluate    player and ball metrics against labels, when label files exist
"""

import hashlib
import json
import shutil
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pickleball_ml.paths import DEFAULT_PROCESSED_ROOT, StageOutputs

CLIP_STAGES = ("metadata", "camera", "calibrate")
WINDOW_STAGES = ("track", "identify", "render", "ball", "ball_render", "evaluate")
ALL_STAGES = CLIP_STAGES + WINDOW_STAGES


@dataclass(frozen=True)
class CameraSegment:
    """A stretch of a clip over which the camera does not move."""

    id: str
    points: Path  # JSON of clicked/measured court landmarks
    background: tuple[float, float]  # seconds, window used for the median background image
    start_s: float = 0.0
    end_s: float | None = None
    notes: str = ""

    def contains(self, start_s: float, end_s: float) -> bool:
        return start_s >= self.start_s and (self.end_s is None or end_s <= self.end_s)


@dataclass(frozen=True)
class Clip:
    id: str
    video: Path
    description: str = ""
    role: str = "tune"  # "tune" or "holdout"
    quality: str = ""  # hand-assigned image-quality category
    segments: tuple[CameraSegment, ...] = ()

    def segment(self, segment_id: str) -> CameraSegment:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment
        raise ValueError(f"clip {self.id}: no camera segment {segment_id!r}")


@dataclass(frozen=True)
class BallWindow:
    """Sub-range of a window to run ball tracking on (ball inference is expensive)."""

    start_s: float
    end_s: float


@dataclass(frozen=True)
class Window:
    id: str
    clip: str
    start_s: float
    end_s: float
    camera: str = "main"
    split: str = "tune"  # "tune" or "test"
    purpose: str = ""
    stride: int | None = None  # detector stride; defaults to the manifest's tracking stride
    end_switches_s: tuple[float, ...] = ()  # times at which the teams have swapped ends
    ball: BallWindow | None = None
    player_labels: Path | None = None
    ball_labels: Path | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class Experiment:
    name: str
    clips: dict[str, Clip]
    windows: tuple[Window, ...]
    tracking: dict[str, Any] = field(default_factory=dict)
    ball: dict[str, Any] = field(default_factory=dict)
    processed_root: Path = DEFAULT_PROCESSED_ROOT
    description: str = ""

    def window(self, window_id: str) -> Window:
        for window in self.windows:
            if window.id == window_id:
                return window
        raise ValueError(f"no window {window_id!r} in experiment {self.name}")

    def clip_root(self, clip_id: str) -> Path:
        return self.processed_root / clip_id

    def outputs(self, window: Window) -> StageOutputs:
        return StageOutputs(self.clip_root(window.clip) / window.id)

    def clip_outputs(self, clip_id: str) -> StageOutputs:
        return StageOutputs(self.clip_root(clip_id))

    def calibration_path(self, clip_id: str, camera: str) -> Path:
        return self.clip_root(clip_id) / f"calibration_{camera}.json"

    def camera_path(self, clip_id: str, camera: str) -> Path:
        return self.clip_root(clip_id) / f"camera_{camera}.json"

    def summary_path(self) -> Path:
        return self.processed_root / "experiments" / f"{self.name}.json"


def load_experiment(path: Path) -> Experiment:
    data = json.loads(path.read_text())
    clips = {}
    for clip_id, raw in data["clips"].items():
        segments = tuple(
            CameraSegment(
                id=str(segment.get("id", "main")),
                points=Path(segment["points"]),
                background=(float(segment["background"][0]), float(segment["background"][1])),
                start_s=float(segment.get("start_s", 0.0)),
                end_s=None if segment.get("end_s") is None else float(segment["end_s"]),
                notes=str(segment.get("notes", "")),
            )
            for segment in raw.get("segments", [])
        )
        clips[clip_id] = Clip(
            id=clip_id, video=Path(raw["video"]), description=str(raw.get("description", "")),
            role=str(raw.get("role", "tune")), quality=str(raw.get("quality", "")),
            segments=segments,
        )
    windows = tuple(
        Window(
            id=str(raw["id"]), clip=str(raw["clip"]), start_s=float(raw["start_s"]),
            end_s=float(raw["end_s"]), camera=str(raw.get("camera", "main")),
            split=str(raw.get("split", "tune")), purpose=str(raw.get("purpose", "")),
            stride=None if raw.get("stride") is None else int(raw["stride"]),
            end_switches_s=tuple(float(s) for s in raw.get("end_switches_s", [])),
            ball=None if raw.get("ball") is None else BallWindow(
                float(raw["ball"]["start_s"]), float(raw["ball"]["end_s"])),
            player_labels=_optional_path(raw.get("player_labels")),
            ball_labels=_optional_path(raw.get("ball_labels")),
        )
        for raw in data["windows"]
    )
    experiment = Experiment(
        name=str(data["name"]), clips=clips, windows=windows,
        tracking=dict(data.get("tracking", {})), ball=dict(data.get("ball", {})),
        processed_root=Path(data.get("processed_root", DEFAULT_PROCESSED_ROOT)),
        description=str(data.get("description", "")),
    )
    validate(experiment)
    return experiment


def _optional_path(value: Any) -> Path | None:
    return None if value in (None, "") else Path(str(value))


def validate(experiment: Experiment) -> None:
    """Fail loudly on manifests that would silently produce meaningless results."""
    seen: set[str] = set()
    for window in experiment.windows:
        if window.id in seen:
            raise ValueError(f"duplicate window id {window.id!r}")
        seen.add(window.id)
        if window.clip not in experiment.clips:
            raise ValueError(f"window {window.id}: unknown clip {window.clip!r}")
        if window.end_s <= window.start_s:
            raise ValueError(f"window {window.id}: end_s must be after start_s")
        clip = experiment.clips[window.clip]
        segment = clip.segment(window.camera)
        if not segment.contains(window.start_s, window.end_s):
            raise ValueError(
                f"window {window.id} ({window.start_s}-{window.end_s}s) is outside camera "
                f"segment {segment.id} ({segment.start_s}-{segment.end_s}s); the camera moved, "
                f"so the calibration would be wrong"
            )
        for switch in window.end_switches_s:
            if not window.start_s < switch < window.end_s:
                raise ValueError(f"window {window.id}: end switch {switch}s is outside the window")
        if window.ball is not None and not (
            window.start_s <= window.ball.start_s < window.ball.end_s <= window.end_s
        ):
            raise ValueError(f"window {window.id}: ball range must lie inside the window")
        if window.split not in ("tune", "test"):
            raise ValueError(f"window {window.id}: split must be 'tune' or 'test'")


def to_frame(seconds: float, fps: float) -> int:
    """Frame number of a time in seconds, assuming a constant frame rate."""
    return int(round(seconds * fps))


def window_frames(window: Window, fps: float) -> tuple[int, int]:
    return to_frame(window.start_s, fps), to_frame(window.end_s, fps)


def fingerprint(payload: Any) -> str:
    """Stable hash of a stage's inputs and configuration."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@dataclass
class StageResult:
    stage: str
    status: str  # "ok", "skipped", "failed"
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        record = {"stage": self.stage, "status": self.status, "seconds": round(self.seconds, 1)}
        if self.detail:
            record["detail"] = self.detail
        if self.error:
            record["error"] = self.error
        return record


class StageRunner:
    """Runs stages, skipping those whose output is present and up to date."""

    def __init__(self, force: Sequence[str] = (), dry_run: bool = False) -> None:
        self.force = set(force)
        self.dry_run = dry_run
        self.results: list[StageResult] = []

    def up_to_date(self, stage: str, marker: Path, expected: str, outputs: Iterable[Path]) -> bool:
        if stage in self.force or "all" in self.force:
            return False
        if not marker.exists() or not all(path.exists() for path in outputs):
            return False
        try:
            recorded = json.loads(marker.read_text()).get("fingerprint")
        except (OSError, json.JSONDecodeError):
            return False
        return bool(recorded) and recorded == expected

    def record(self, result: StageResult) -> StageResult:
        self.results.append(result)
        return result

    def skip(self, stage: str, detail: dict[str, Any] | None = None) -> StageResult:
        return self.record(StageResult(stage, "skipped", detail=detail or {}))

    def run(self, stage: str, work: Any) -> StageResult:
        started = time.monotonic()
        try:
            detail = work() or {}
        except Exception as exc:  # a failed window must not abort the experiment
            return self.record(StageResult(stage, "failed", time.monotonic() - started,
                                           error=f"{type(exc).__name__}: {exc}"))
        return self.record(StageResult(stage, "ok", time.monotonic() - started, detail=detail))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def copy_calibration(source: Path, outputs: StageOutputs) -> None:
    """Snapshot the camera calibration into the window directory the stages read from."""
    outputs.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, outputs.calibration)


def window_record(
    experiment: Experiment, window: Window, metadata: dict[str, Any],
    frames: tuple[int, int], calibration: dict[str, Any], stages: list[StageResult],
) -> dict[str, Any]:
    clip = experiment.clips[window.clip]
    fps = float(metadata["fps"])
    failed = [s.stage for s in stages if s.status == "failed"]
    return {
        "experiment": experiment.name,
        "window": {**asdict(window), "duration_s": round(window.duration_s, 2)},
        "clip": {"id": clip.id, "video": str(clip.video), "role": clip.role,
                 "quality": clip.quality, "description": clip.description},
        "camera_segment": asdict(clip.segment(window.camera)),
        "frames": {"start": frames[0], "end": frames[1], "fps": fps,
                   "start_timestamp_s": round(frames[0] / fps, 3),
                   "end_timestamp_s": round(frames[1] / fps, 3)},
        "source_metadata": metadata,
        "calibration": calibration,
        "end_switch_frames": [to_frame(s, fps) for s in window.end_switches_s],
        "stages": [s.to_dict() for s in stages],
        "status": "failed" if failed else "ok",
        "failed_stages": failed,
        "artifacts": _existing_artifacts(experiment.outputs(window)),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _existing_artifacts(outputs: StageOutputs) -> dict[str, str]:
    candidates = {
        "detections_raw": outputs.detections_raw, "players_court": outputs.players_court,
        "topdown_video": outputs.topdown_video, "ball_raw": outputs.ball_raw,
        "ball_track": outputs.ball_track, "ball_video": outputs.ball_video,
        "calibration": outputs.calibration,
    }
    return {name: str(path) for name, path in candidates.items() if path.exists()}


def smoke_window(window: Window, seconds: float) -> Window:
    """A short version of a window, written to its own directory."""
    end = min(window.end_s, window.start_s + seconds)
    ball = None if window.ball is None else BallWindow(window.start_s, end)
    return replace(window, id=f"{window.id}_smoke", end_s=end, ball=ball,
                   end_switches_s=tuple(s for s in window.end_switches_s if s < end),
                   player_labels=None, ball_labels=None,
                   purpose=f"smoke test of {window.id}")
