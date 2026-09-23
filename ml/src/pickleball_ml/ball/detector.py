"""Ball detectors behind one interface.

A detector turns an explicit frame window of a video into candidate ball
positions in *source image pixels*, keeping the source frame numbers and
timestamps. It never invents a position: a frame with no candidate above the
score threshold simply has no rows.

Two baselines are implemented:

WASB (`wasb`)
    The published "Widely Applicable Strong Baseline" for sports ball detection
    and tracking (Tarashima et al., BMVC 2023): a small HRNet that takes three
    consecutive frames and predicts one heatmap per frame. Pretrained weights
    for tennis and badminton are released by the authors; there is no
    pickleball model, so this is a cross-sport transfer. Code MIT
    (github.com/nttcom/WASB-SBDT), architecture vendored in `wasb_hrnet.py`.

YOLO sports ball (`yolo_sports_ball`)
    The COCO "sports ball" class of the pretrained detector already used for
    players. No extra download and no temporal information, which makes it a
    fair "does a general detector suffice?" comparison rather than a serious
    contender.

Both report a confidence, which is only comparable within one detector.
"""

import hashlib
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, cast

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.video.reader import Frame, iter_frames

CANDIDATE_COLUMNS = ["frame_number", "timestamp_ms", "candidate", "x", "y", "score"]

# WASB's released model configuration (configs/model/wasb.yaml in the upstream repo).
WASB_MODEL_CONFIG: dict[str, Any] = {
    "name": "hrnet", "frames_in": 3, "frames_out": 3,
    "inp_height": 288, "inp_width": 512, "out_height": 288, "out_width": 512,
    "out_scales": [0], "rgb_diff": False,
    "MODEL": {"EXTRA": {
        "FINAL_CONV_KERNEL": 1, "PRETRAINED_LAYERS": ["*"],
        "STEM": {"INPLANES": 64, "STRIDES": [1, 1]},
        "STAGE1": {"NUM_MODULES": 1, "NUM_BRANCHES": 1, "BLOCK": "BOTTLENECK",
                   "NUM_BLOCKS": [1], "NUM_CHANNELS": [32], "FUSE_METHOD": "SUM"},
        "STAGE2": {"NUM_MODULES": 1, "NUM_BRANCHES": 2, "BLOCK": "BASIC",
                   "NUM_BLOCKS": [2, 2], "NUM_CHANNELS": [16, 32], "FUSE_METHOD": "SUM"},
        "STAGE3": {"NUM_MODULES": 1, "NUM_BRANCHES": 3, "BLOCK": "BASIC",
                   "NUM_BLOCKS": [2, 2, 2], "NUM_CHANNELS": [16, 32, 64], "FUSE_METHOD": "SUM"},
        "STAGE4": {"NUM_MODULES": 1, "NUM_BRANCHES": 4, "BLOCK": "BASIC",
                   "NUM_BLOCKS": [2, 2, 2, 2], "NUM_CHANNELS": [16, 32, 64, 128],
                   "FUSE_METHOD": "SUM"},
        "DECONV": {"NUM_DECONVS": 0, "KERNEL_SIZE": [], "NUM_BASIC_BLOCKS": 2},
    }, "INIT_WEIGHTS": True},
}
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True)
class BallDetectorConfig:
    detector: str = "wasb"
    weights: str = "weights/wasb/wasb_tennis_best.pth.tar"
    device: str = "mps"
    score_threshold: float = 0.05  # low on purpose: filtering and evaluation come later
    max_candidates: int = 5
    # The WASB models were trained on ~30 fps footage, so 60 fps input is
    # subsampled to keep the apparent ball motion between frames similar.
    target_fps: float = 30.0
    imgsz: int = 1280  # yolo_sports_ball only
    conf: float = 0.05  # yolo_sports_ball only


class BallDetector(Protocol):
    """Candidate ball positions in source-image pixels, frame by frame."""

    name: str

    def detect(
        self, video: Path, start_frame: int, end_frame: int, stride: int
    ) -> Iterator[tuple[int, float, list[tuple[float, float, float]]]]:
        """Yield (frame_number, timestamp_ms, [(x, y, score), ...]) for each processed frame."""
        ...

    def run_metadata(self) -> dict[str, Any]:
        ...


def build_detector(config: BallDetectorConfig) -> BallDetector:
    if config.detector == "wasb":
        return WasbDetector(config)
    if config.detector == "yolo_sports_ball":
        return YoloSportsBallDetector(config)
    raise ValueError(f"unknown ball detector {config.detector!r}")


def detect_ball(
    video: Path, config: BallDetectorConfig, start_frame: int, end_frame: int, stride: int,
    progress: Any = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run a detector over a frame window; returns candidate rows and a run record."""
    detector = build_detector(config)
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    processed = 0
    for frame_number, timestamp_ms, candidates in detector.detect(
        video, start_frame, end_frame, stride
    ):
        kept = candidates[: config.max_candidates]
        if not kept:
            # Keep a row so later stages know the frame was processed and nothing was found.
            rows.append({"frame_number": frame_number, "timestamp_ms": timestamp_ms,
                         "candidate": -1, "x": np.nan, "y": np.nan, "score": np.nan})
        for index, (x, y, score) in enumerate(kept):
            rows.append({"frame_number": frame_number, "timestamp_ms": timestamp_ms,
                         "candidate": index, "x": float(x), "y": float(y),
                         "score": float(score)})
        processed += 1
        if progress is not None:
            progress(frame_number, processed / max(time.monotonic() - started, 1e-9))
    elapsed = time.monotonic() - started
    candidates_frame = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    run = {
        "stage": "ball_detection",
        "config": asdict(config),
        "video": str(video),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "stride": stride,
        "frames_processed": processed,
        "frames_with_candidate": int(
            candidates_frame.loc[candidates_frame["candidate"] >= 0, "frame_number"].nunique()
        ),
        "candidates": len(candidates_frame),
        "elapsed_seconds": round(elapsed, 1),
        "frames_per_second": round(processed / elapsed, 2) if elapsed > 0 else None,
        "created_at": datetime.now(UTC).isoformat(),
        **detector.run_metadata(),
    }
    return candidates_frame, run


class WasbDetector:
    """Three-frame heatmap detector (WASB / HRNet)."""

    name = "wasb"

    def __init__(self, config: BallDetectorConfig) -> None:
        self.config = config
        self.weights = Path(config.weights)

    def detect(
        self, video: Path, start_frame: int, end_frame: int, stride: int
    ) -> Iterator[tuple[int, float, list[tuple[float, float, float]]]]:
        import torch  # heavy import; keep module import cheap

        from pickleball_ml.ball.wasb_hrnet import HRNet

        model = cast(Any, HRNet)(WASB_MODEL_CONFIG)
        checkpoint = torch.load(self.weights, map_location="cpu", weights_only=False)
        model.load_state_dict(checkpoint.get("model_state_dict", checkpoint))
        model.eval().to(self.config.device)

        frames_in = int(WASB_MODEL_CONFIG["frames_in"])
        width = int(WASB_MODEL_CONFIG["inp_width"])
        height = int(WASB_MODEL_CONFIG["inp_height"])
        buffer: list[tuple[int, float, np.ndarray]] = []
        source_size: tuple[int, int] | None = None

        with torch.no_grad():
            for frame_number, timestamp_ms, image in iter_frames(
                video, start_frame, end_frame, stride
            ):
                if source_size is None:
                    source_size = (image.shape[1], image.shape[0])
                buffer.append((frame_number, timestamp_ms, _preprocess(image, width, height)))
                if len(buffer) < frames_in:
                    continue
                batch = torch.from_numpy(
                    np.concatenate([item[2] for item in buffer])[None]
                ).to(self.config.device)
                heatmaps = torch.sigmoid(model(batch)[0]).cpu().numpy()[0]
                for i, (number, stamp, _) in enumerate(buffer):
                    yield number, stamp, _heatmap_candidates(
                        heatmaps[i], source_size, self.config.score_threshold
                    )
                buffer = []

        # A trailing 1-2 frames cannot form a full triple; report them as unprocessed
        # rather than padding with duplicates, which would fabricate motion.

    def run_metadata(self) -> dict[str, Any]:
        from importlib.metadata import version as _version
        return {
            "model_name": f"wasb:{self.weights.name}",
            "model_sha256": _sha256(self.weights),
            "model_source": "https://github.com/nttcom/WASB-SBDT (MIT)",
            "model_input": f"{WASB_MODEL_CONFIG['frames_in']} frames, "
                           f"{WASB_MODEL_CONFIG['inp_width']}x{WASB_MODEL_CONFIG['inp_height']}",
            "torch_version": _version("torch"),
        }


def _preprocess(image: Frame, width: int, height: int) -> np.ndarray:
    resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return ((rgb - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)


def _heatmap_candidates(
    heatmap: np.ndarray, source_size: tuple[int, int], threshold: float
) -> list[tuple[float, float, float]]:
    """Blob centres of a heatmap, scaled to source pixels, strongest first.

    Connected components above the threshold, each scored by its summed heat and
    located at its heat-weighted centroid (as in the WASB postprocessor).
    """
    if float(heatmap.max()) <= threshold:
        return []
    mask = (heatmap > threshold).astype(np.uint8)
    count, labels = cv2.connectedComponents(mask)
    scale_x = source_size[0] / heatmap.shape[1]
    scale_y = source_size[1] / heatmap.shape[0]
    candidates = []
    for label in range(1, count):
        ys, xs = np.where(labels == label)
        weights = heatmap[ys, xs].astype(np.float64)
        total = float(weights.sum())
        x = float((xs * weights).sum() / total)
        y = float((ys * weights).sum() / total)
        candidates.append((x * scale_x, y * scale_y, float(heatmap[ys, xs].max())))
    # Rank by peak height: comparable between frames, unlike summed blob heat.
    return sorted(candidates, key=lambda c: -c[2])


class YoloSportsBallDetector:
    """COCO 'sports ball' detections from the pretrained YOLO detector."""

    name = "yolo_sports_ball"
    SPORTS_BALL_CLASS = 32

    def __init__(self, config: BallDetectorConfig) -> None:
        self.config = config
        self.weights = Path(config.weights)

    def detect(
        self, video: Path, start_frame: int, end_frame: int, stride: int
    ) -> Iterator[tuple[int, float, list[tuple[float, float, float]]]]:
        from ultralytics import YOLO

        model = YOLO(str(self.weights))
        for frame_number, timestamp_ms, image in iter_frames(
            video, start_frame, end_frame, stride
        ):
            result = model.predict(image, classes=[self.SPORTS_BALL_CLASS],
                                   imgsz=self.config.imgsz, conf=self.config.conf,
                                   device=self.config.device, verbose=False)[0]
            boxes = result.boxes.cpu().numpy()
            xyxy = np.asarray(boxes.xyxy, dtype=np.float64).reshape(-1, 4)
            candidates = [
                ((x1 + x2) / 2.0, (y1 + y2) / 2.0, float(score))
                for (x1, y1, x2, y2), score in zip(xyxy, np.asarray(boxes.conf).ravel(),
                                                   strict=True)
            ]
            yield frame_number, timestamp_ms, sorted(candidates, key=lambda c: -c[2])

    def run_metadata(self) -> dict[str, Any]:
        return {
            "model_name": f"yolo_sports_ball:{self.weights.name}",
            "model_sha256": _sha256(self.weights),
            "model_source": "ultralytics pretrained COCO detector",
            "ultralytics_version": version("ultralytics"),
            "torch_version": version("torch"),
        }


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def processed_frames(candidates: pd.DataFrame) -> tuple[list[int], dict[int, float]]:
    """Frame numbers the detector processed, in order, and their timestamps."""
    ordered = candidates.drop_duplicates("frame_number").sort_values("frame_number")
    frames = [int(f) for f in ordered["frame_number"]]
    timestamps = {int(f): float(t) for f, t in zip(ordered["frame_number"],
                                                   ordered["timestamp_ms"], strict=True)}
    return frames, timestamps


def stride_for_target_fps(source_fps: float, target_fps: float) -> int:
    """Frame stride that brings `source_fps` closest to `target_fps`.

    Rounds to nearest so 59.94 fps footage is subsampled by 2 rather than left at
    full rate, which would halve the apparent ball motion the model expects.
    """
    if target_fps <= 0:
        return 1
    return max(1, round(source_fps / target_fps))
