"""What the API returns.

These are separate from the SQLAlchemy models on purpose: the tables carry
things the outside world must not see, `storage_key` above all. A response
model that is built by hand cannot leak a column somebody adds later.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Match, MatchStatus


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


class MatchDetail(MatchSummary):
    """One match and every attempt made at processing it, oldest first."""

    jobs: list[JobRead]
    #: Whether `POST /api/matches/{id}/metadata-jobs` would accept a request
    #: right now: the metadata is absent and no job is queued or running.
    can_extract_metadata: bool

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
