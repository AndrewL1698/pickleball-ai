"""Locations of per-video stage outputs."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROCESSED_ROOT = Path("data/processed")


@dataclass(frozen=True)
class StageOutputs:
    """Files written by each pipeline stage for one source video."""

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
