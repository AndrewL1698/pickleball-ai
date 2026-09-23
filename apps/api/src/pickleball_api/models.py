"""Database tables for uploaded video and the jobs that analyze it.

`docs/DATA_MODEL.md` describes the eventual schema, where a `Match` owns a
`VideoAsset` and a `ProcessingJob`. Phase 1 checkpoint 1 carries only the two
tables the upload path needs: `videos` (the stored file) and `analysis_jobs`
(one attempt at processing it). `Match` and the per-stage pipeline tables
arrive with the stages that populate them.

Identifiers are UUIDs rather than sequence numbers: they appear in URLs, there
is no authorization yet, and a guessable id would be an invitation to walk the
whole table.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base; `Base.metadata` is what Alembic autogenerates against."""


def utcnow() -> datetime:
    """Timestamps are generated in Python, not by the database.

    `func.now()` is kept as a server default for rows inserted by hand, but the
    application never relies on it: PostgreSQL's `now()` is the transaction's
    start time, so two rows written in one transaction get identical values,
    and SQLite's `CURRENT_TIMESTAMP` resolves only to the second and drops the
    timezone. Both make "the most recent job" ambiguous.
    """
    return datetime.now(UTC)


class JobStatus(enum.StrEnum):
    """Lifecycle of one analysis attempt."""

    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class JobStage(enum.StrEnum):
    """Pipeline stage a job has reached.

    The names follow the stage sequence in `docs/ARCHITECTURE.md`. Checkpoint 1
    only reaches `METADATA_READY`, because the processor is a placeholder; the
    later stages are listed so the column's meaning does not change when the
    real computer-vision stages are wired in.
    """

    INGESTED = "ingested"
    METADATA_READY = "metadata_ready"
    COURT_READY = "court_ready"
    PLAYERS_READY = "players_ready"
    BALL_READY = "ball_ready"
    RALLIES_READY = "rallies_ready"
    ANALYTICS_READY = "analytics_ready"


def _enum_column(enum_class: type[enum.StrEnum], name: str) -> Enum:
    """A portable enum column: VARCHAR plus a CHECK constraint, storing the values.

    Native PostgreSQL enums need a migration to add a member and do not exist on
    SQLite, which the test suite runs on. Storing the lowercase values keeps the
    rows readable in `psql`.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        # SQLAlchemy defaults this to False for a non-native enum, so without
        # it the column is a bare VARCHAR and the constraint the docstring
        # promises does not exist.
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )


class Video(Base):
    """An uploaded video file, recorded once its bytes are safely in storage."""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    # BigInteger: the default upload limit alone is 2 GiB, which is past INT4.
    byte_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )

    jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="AnalysisJob.created_at, AnalysisJob.id",
    )

    __table_args__ = (CheckConstraint("byte_size >= 0", name="ck_videos_byte_size_non_negative"),)

    @property
    def latest_job(self) -> "AnalysisJob | None":
        """The most recently created job, which is the one the UI should show.

        `jobs` is ordered by the relationship, so this is the last of them; a
        dead heat on the timestamp is broken by id, arbitrarily but stably.
        """
        return self.jobs[-1] if self.jobs else None


class AnalysisJob(Base):
    """One attempt at processing a video, and how far it got.

    `error_code` is a stable machine-readable reason (see
    `pickleball_api.errors.JobErrorCode`); `error_message` is a short sanitized
    sentence. Neither ever carries a traceback or a filesystem path — those are
    logged on the server instead.
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    stage: Mapped[JobStage] = mapped_column(
        _enum_column(JobStage, "job_stage"), default=JobStage.INGESTED
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    video: Mapped[Video] = relationship(back_populates="jobs")

    __table_args__ = (
        CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="ck_analysis_jobs_progress"),
        Index("ix_analysis_jobs_video_id_created_at", "video_id", "created_at"),
    )
