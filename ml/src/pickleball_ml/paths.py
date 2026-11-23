"""Locations of per-video and per-window stage outputs.

Phase 0a writes one directory per source video: data/processed/<video_stem>/.
Phase 0b experiments process several windows of one source video, so each
window gets its own directory (data/processed/<clip_id>/<window_id>/) with the
same file names inside; see pickleball_ml.experiment.
"""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROCESSED_ROOT = Path("data/processed")
DEFAULT_ANNOTATIONS_ROOT = Path("data/annotations")


def ground_contact_labels_path(clip_id: str) -> Path:
    """Hand-clicked ground-contact points for one clip, across all its windows.

    The labeling tool writes here and the evaluate stage reads from here.
    """
    return DEFAULT_ANNOTATIONS_ROOT / clip_id / "ground_contact_labels.json"


@dataclass(frozen=True)
class StageOutputs:
    """Files written by each pipeline stage for one source video or one window of it."""

    root: Path

    @classmethod
    def for_video(
        cls, video: Path, processed_root: Path = DEFAULT_PROCESSED_ROOT
    ) -> "StageOutputs":
        return cls(processed_root / video.stem)

    @property
    def metadata(self) -> Path:
        return self.root / "metadata.json"

    @property
    def calibration(self) -> Path:
        return self.root / "calibration.json"

    @property
    def calibration_overlay(self) -> Path:
        return self.root / "calibration_overlay.png"

    @property
    def detections_raw(self) -> Path:
        return self.root / "detections_raw.parquet"

    @property
    def detections_run(self) -> Path:
        return self.root / "detections_raw.run.json"

    @property
    def players_court(self) -> Path:
        return self.root / "players_court.parquet"

    @property
    def players_run(self) -> Path:
        return self.root / "players_court.run.json"

    @property
    def topdown_video(self) -> Path:
        return self.root / "topdown.mp4"

    @property
    def ball_raw(self) -> Path:
        return self.root / "ball_raw.parquet"

    @property
    def ball_track(self) -> Path:
        return self.root / "ball_track.parquet"

    @property
    def ball_run(self) -> Path:
        return self.root / "ball_track.run.json"

    @property
    def ball_video(self) -> Path:
        return self.root / "ball_debug.mp4"

    @property
    def window_record(self) -> Path:
        """Experiment run record for a window: inputs, configs, runtimes, artifacts, status."""
        return self.root / "window_run.json"
