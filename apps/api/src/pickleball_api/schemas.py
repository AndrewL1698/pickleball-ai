"""What the API returns.

These are separate from the SQLAlchemy models on purpose: the tables carry
things the outside world must not see, `storage_key` above all. A response
model that is built by hand cannot leak a column somebody adds later.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, computed_field

from pickleball_api.calibration import (
    CourtLandmark,
    calibration_warnings,
    far_baseline_feet_per_pixel,
    projected_court_lines,
)
from pickleball_api.models import (
    AnalysisJob,
    CalibrationSource,
    JobStage,
    JobStatus,
    Match,
    MatchStatus,
    ReferenceFrameKind,
)


class JobRead(BaseModel):
    """An analysis job's progress and outcome."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    match_id: UUID
    status: JobStatus
    stage: JobStage
    progress: float = Field(ge=0.0, le=1.0)
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, job: AnalysisJob) -> "JobRead":
        return cls.model_validate(job)


class VideoMetadataRead(BaseModel):
    """What the worker decoded from the video.

    `width` and `height` are in display orientation -- the way the video plays
    -- after applying `rotation_degrees`, the container's rotation tag.
    `average_fps` is an average, not exact frame timing: phone video is often
    variable frame rate. `duration_seconds` is frame count over that average,
    so it is an estimate on the same terms. `codec` is the stream's FourCC, or
    null when it does not name one.
    """

    model_config = ConfigDict(from_attributes=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation_degrees: Literal[0, 90, 180, 270]
    average_fps: float = Field(gt=0)
    duration_seconds: float = Field(ge=0)
    frame_count: int = Field(ge=0)
    codec: str | None
    extracted_at: datetime


class CameraJumpRead(BaseModel):
    """The camera moved somewhere between these two sampled moments."""

    from_seconds: float
    to_seconds: float
    #: How far the picture shifted, in the video's pixels.
    shift_px: float


class VideoRead(BaseModel):
    """The stored video behind a match, minus where it is stored."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    content_type: str
    byte_size: int
    created_at: datetime
    #: Null until a processing job has decoded the video -- including every
    #: video uploaded before metadata extraction existed. Never zero-filled.
    metadata: VideoMetadataRead | None = Field(validation_alias="decoded_metadata")
    #: Where sampled frames show the camera moved. Empty means none was found, and
    #: null means not checked. A move and back between two samples would not show.
    camera_jumps: list[CameraJumpRead] | None


class MatchSummary(BaseModel):
    """A match in a list: its status, its video, and its most recent job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    recorded_at: datetime | None
    status: MatchStatus
    created_at: datetime
    #: Null only for a match whose video row is missing, which the upload
    #: path never produces; the schema says so rather than inventing one.
    video: VideoRead | None
    latest_job: JobRead | None

    @classmethod
    def of(cls, match: Match) -> "MatchSummary":
        return cls.model_validate(match)


class ReferenceFrameRead(BaseModel):
    """A still to place calibration landmarks on, minus where it is stored.

    `width` and `height` are its pixels, which are the video's own in display
    orientation. `position_seconds` is approximate, and null for a background.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: ReferenceFrameKind
    position_seconds: float | None
    source_frame_count: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: datetime


class CalibrationCreate(BaseModel):
    """Landmarks placed on one reference frame, in that frame's pixels."""

    model_config = ConfigDict(extra="forbid")

    reference_frame_id: UUID
    image_points: dict[CourtLandmark, tuple[FiniteFloat, FiniteFloat]]


class CalibrationRead(BaseModel):
    """A fitted calibration, and what is known about its quality.

    `homography` maps image pixels to court feet, and is valid for points on the
    ground plane only. The error is how far each landmark lands from where it
    should, which says how well the clicks agree, not how accurate a position is.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference_frame_id: UUID
    source: CalibrationSource
    image_points: dict[str, tuple[float, float]]
    court_points: dict[str, tuple[float, float]]
    homography: list[list[float]]
    reprojection_error_ft: dict[str, float]
    mean_reprojection_error_ft: float = Field(ge=0)
    max_reprojection_error_ft: float = Field(ge=0)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def projected_lines(self) -> dict[str, list[tuple[float, float]]]:
        """Each court line's two ends in image pixels, for drawing an overlay."""
        return projected_court_lines(self.homography)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def far_baseline_ft_per_pixel(self) -> float | None:
        """The most one image pixel can be worth at the far baseline, in feet."""
        return far_baseline_feet_per_pixel(self.homography)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> list[str]:
        """Fixed sentences for anything a person should review. Empty if nothing."""
        return calibration_warnings(
            self.court_points, self.mean_reprojection_error_ft, self.max_reprojection_error_ft
        )


class MatchDetail(MatchSummary):
    """One match and every attempt made at processing it, oldest first."""

    jobs: list[JobRead]
    reference_frames: list[ReferenceFrameRead]
    #: The calibration in force, or null if the court has not been calibrated.
    calibration: CalibrationRead | None = Field(validation_alias="latest_calibration")
    #: Whether `POST /api/matches/{id}/metadata-jobs` would accept a request
    #: right now: the metadata is absent and no job is queued or running.
    can_extract_metadata: bool
    #: Whether `POST /api/matches/{id}/reference-frame-jobs` would accept a
    #: request: there is metadata, no calibration images, and no active job.
    can_extract_reference_frames: bool
    #: Whether `POST /api/matches/{id}/calibrations` would accept a request.
    can_calibrate: bool

    @classmethod
    def of(cls, match: Match) -> "MatchDetail":
        return cls.model_validate(match)


class MatchList(BaseModel):
    matches: list[MatchSummary]
    #: The length of this page, not a total across all matches.
    count: int


class HealthResponse(BaseModel):
    """Liveness: this process is answering."""

    status: Literal["ok"] = "ok"
    environment: str


class ReadyResponse(BaseModel):
    """Readiness: the dependencies this process needs are reachable.

    Deliberately booleans and nothing else. The reason a check failed is logged
    on the server: a connection error's text names the host it failed to reach,
    and nothing about the infrastructure belongs in a public response.
    """

    ready: bool
    database: bool
    redis: bool


class ErrorResponse(BaseModel):
    """The single shape every error uses."""

    error_code: str
    detail: str
