"""Database tables for matches, their uploaded video, and the jobs that analyze them.

`docs/DATA_MODEL.md` describes the eventual schema. A `Match` is the parent of
everything recorded about one game of pickleball: today its `Video` (the stored
file, one per match for the MVP) and its `AnalysisJob`s (each an attempt at
processing it); later its calibration, players and rallies. Ownership points
Match -> Video rather than the other way round, so those later tables have a
stable parent that does not change if a match ever gains a second video.

Identifiers are UUIDs rather than sequence numbers: they appear in URLs, there
is no authorization yet, and a guessable id would be an invitation to walk the
whole table.
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
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


class MatchStatus(enum.StrEnum):
    """Where a match is in its lifecycle, in words a user can act on.

    There is deliberately no generic `ready`: it would read as "the match has
    been analysed", which is several phases away. Each state names what has
    actually happened, or what has to happen next.
    """

    #: Stored and recorded; no processing attempt has started.
    UPLOADED = "uploaded"
    #: A worker is processing the video.
    PROCESSING = "processing"
    #: Processing finished; the court has to be calibrated before anything
    #: that needs court coordinates can run.
    CALIBRATION_REQUIRED = "calibration_required"
    #: A court calibration exists. Nothing sets this until the calibration
    #: checkpoint lands; it is here so the vocabulary does not change then.
    COURT_READY = "court_ready"
    #: The latest processing attempt failed.
    FAILED = "failed"


#: The job statuses that mean "a worker has, or is about to have, this match".
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)

#: SQL for the partial unique index that allows one active job per match.
#: Literal, because it is part of the schema: migration 0004 carries a copy.
ACTIVE_JOB_PREDICATE = "status IN ('queued', 'running')"


class MetadataJobRefusal(enum.StrEnum):
    """Why a match cannot be given a new metadata extraction job."""

    NO_VIDEO = "no_video"
    JOB_ACTIVE = "job_active"
    ALREADY_EXTRACTED = "already_extracted"


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


class Match(Base):
    """One recorded game, and the owner of everything derived from it."""

    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    #: When the game was played, if anyone says. Not the upload time.
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status: Mapped[MatchStatus] = mapped_column(
        _enum_column(MatchStatus, "match_status"), default=MatchStatus.UPLOADED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )

    video: Mapped["Video | None"] = relationship(
        back_populates="match", cascade="all, delete-orphan", uselist=False
    )
    jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="AnalysisJob.created_at, AnalysisJob.id",
    )

    @property
    def latest_job(self) -> "AnalysisJob | None":
        """The most recently created job, which is the one the UI should show.

        `jobs` is ordered by the relationship, so this is the last of them; a
        dead heat on the timestamp is broken by id, arbitrarily but stably.
        """
        return self.jobs[-1] if self.jobs else None

    def metadata_job_refusal(self) -> MetadataJobRefusal | None:
        """Why a new metadata job may not start, or None if it may.

        A job may start when the video's metadata has never been extracted --
        which covers a previous failure and every match backfilled from Phase 1
        -- and nothing is already queued or running for the match. Here so the
        API and the `can_extract_metadata` flag the UI reads cannot disagree.
        The partial unique index on `analysis_jobs` is the backstop against two
        requests racing past this check.
        """
        if self.video is None:
            return MetadataJobRefusal.NO_VIDEO
        if any(job.status in ACTIVE_JOB_STATUSES for job in self.jobs):
            return MetadataJobRefusal.JOB_ACTIVE
        if self.video.metadata_extracted_at is not None:
            return MetadataJobRefusal.ALREADY_EXTRACTED
        return None

    @property
    def can_extract_metadata(self) -> bool:
        return self.metadata_job_refusal() is None


@dataclass(frozen=True)
class DecodedVideoMetadata:
    """A video's decoded metadata, present only once all of it is known.

    `width` and `height` are in display orientation, the way the video plays;
    `rotation_degrees` is the container's rotation tag that the decoder applied
    to get there. `average_fps` is an average: phone video is often variable
    frame rate, so it does not give exact frame timing, and `duration_seconds`
    (frame count over average rate) is an estimate on the same terms.
    """

    width: int
    height: int
    rotation_degrees: int
    average_fps: float
    duration_seconds: float
    frame_count: int
    codec: str | None
    extracted_at: datetime


class Video(Base):
    """An uploaded video file, recorded once its bytes are safely in storage."""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE", name="fk_videos_match_id_matches")
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    # BigInteger: the default upload limit alone is 2 GiB, which is past INT4.
    byte_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )

    # Decoded by the worker, never the upload request. All null until a job
    # succeeds -- rows from Phase 1 were never decoded -- and written together
    # with the job's terminal transition. Never zero-filled on failure.
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    rotation_degrees: Mapped[int | None] = mapped_column(Integer, default=None)
    average_fps: Mapped[float | None] = mapped_column(Float, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    frame_count: Mapped[int | None] = mapped_column(Integer, default=None)
    #: The FourCC, when the stream names one; may be null even once decoded.
    codec: Mapped[str | None] = mapped_column(String(32), default=None)
    metadata_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    match: Mapped[Match] = relationship(back_populates="video")

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_videos_byte_size_non_negative"),
        # One video per match for the MVP. Dropping this constraint is the
        # whole migration if a match ever needs a second camera.
        UniqueConstraint("match_id", name="uq_videos_match_id"),
        # Each value is valid on its own terms, and null is always allowed...
        CheckConstraint("width IS NULL OR width > 0", name="ck_videos_width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="ck_videos_height_positive"),
        CheckConstraint(
            "average_fps IS NULL OR average_fps > 0", name="ck_videos_average_fps_positive"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_videos_duration_non_negative",
        ),
        CheckConstraint(
            "frame_count IS NULL OR frame_count >= 0", name="ck_videos_frame_count_non_negative"
        ),
        CheckConstraint(
            "rotation_degrees IS NULL OR rotation_degrees IN (0, 90, 180, 270)",
            name="ck_videos_rotation_right_angle",
        ),
        # ...but the decoded fields arrive together or not at all, so a
        # half-written record cannot pass for metadata. `codec` is exempt.
        CheckConstraint(
            "(metadata_extracted_at IS NULL AND width IS NULL AND height IS NULL"
            " AND rotation_degrees IS NULL AND average_fps IS NULL"
            " AND duration_seconds IS NULL AND frame_count IS NULL AND codec IS NULL)"
            " OR (metadata_extracted_at IS NOT NULL AND width IS NOT NULL"
            " AND height IS NOT NULL AND rotation_degrees IS NOT NULL"
            " AND average_fps IS NOT NULL AND duration_seconds IS NOT NULL"
            " AND frame_count IS NOT NULL)",
            name="ck_videos_metadata_complete",
        ),
    )

    @property
    def decoded_metadata(self) -> DecodedVideoMetadata | None:
        """The metadata as one value, or None if it has not been extracted."""
        if self.metadata_extracted_at is None:
            return None
        # The CHECK constraint guarantees these once the timestamp is set.
        assert self.width is not None and self.height is not None
        assert self.rotation_degrees is not None and self.average_fps is not None
        assert self.duration_seconds is not None and self.frame_count is not None
        return DecodedVideoMetadata(
            width=self.width,
            height=self.height,
            rotation_degrees=self.rotation_degrees,
            average_fps=self.average_fps,
            duration_seconds=self.duration_seconds,
            frame_count=self.frame_count,
            codec=self.codec,
            extracted_at=self.metadata_extracted_at,
        )


class AnalysisJob(Base):
    """One attempt at processing a match's video, and how far it got.

    `error_code` is a stable machine-readable reason (see
    `pickleball_api.errors.JobErrorCode`); `error_message` is a short sanitized
    sentence. Neither ever carries a traceback or a filesystem path — those are
    logged on the server instead.
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE", name="fk_analysis_jobs_match_id_matches")
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

    match: Mapped[Match] = relationship(back_populates="jobs")

    __table_args__ = (
        CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="ck_analysis_jobs_progress"),
        # Serves both "every job for this match" and "its latest job".
        Index("ix_analysis_jobs_match_id_created_at", "match_id", "created_at"),
        # At most one queued or running job per match, enforced by the
        # database: two retry requests racing past the application's check
        # cannot both commit. A partial index, so job history is unaffected.
        Index(
            "ux_analysis_jobs_one_active_per_match",
            "match_id",
            unique=True,
            postgresql_where=text(ACTIVE_JOB_PREDICATE),
            sqlite_where=text(ACTIVE_JOB_PREDICATE),
        ),
    )
