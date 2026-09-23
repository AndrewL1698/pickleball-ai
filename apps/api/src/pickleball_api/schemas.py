"""What the API returns.

These are separate from the SQLAlchemy models on purpose: the tables carry
things the outside world must not see, `storage_key` above all. A response
model that is built by hand cannot leak a column somebody adds later.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Video


class JobRead(BaseModel):
    """An analysis job's progress and outcome."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    video_id: UUID
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


class VideoSummary(BaseModel):
    """A video in a list, with the status of its most recent job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    content_type: str
    byte_size: int
    created_at: datetime
    latest_job: JobRead | None

    @classmethod
    def of(cls, video: Video) -> "VideoSummary":
        return cls.model_validate(video)


class VideoDetail(VideoSummary):
    """One video and every attempt made at processing it."""

    jobs: list[JobRead]

    @classmethod
    def of(cls, video: Video) -> "VideoDetail":
        return cls.model_validate(video)


class VideoList(BaseModel):
    videos: list[VideoSummary]
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
