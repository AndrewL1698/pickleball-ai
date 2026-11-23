"""Database tables for matches, their uploaded video, and the jobs that analyze them.

`docs/DATA_MODEL.md` describes the eventual schema. A `Match` is the parent of
everything recorded about one uploaded video of doubles play, which may hold
several games (`GLOSSARY.md`): its `Video` (the stored file, one per match for
the MVP), its `AnalysisJob`s (each an attempt at processing it), its calibration
and its `ModelRun`s; later its players and rallies. Ownership points
Match -> Video rather than the other way round, so those later tables have a
stable parent that does not change if a match ever gains a second video.

Identifiers are UUIDs rather than sequence numbers: they appear in URLs, there
is no authorization yet, and a guessable id would be an invitation to walk the
whole table.
"""

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


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
    #: A court calibration exists.
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


class ReferenceFrameJobRefusal(enum.StrEnum):
    """Why a match that has metadata cannot be given calibration images."""

    NO_VIDEO = "no_video"
    NO_METADATA = "no_metadata"
    JOB_ACTIVE = "job_active"
    ALREADY_EXTRACTED = "already_extracted"


class CalibrationRefusal(enum.StrEnum):
    """Why a match cannot be calibrated right now."""

    NO_REFERENCE_FRAMES = "no_reference_frames"
    JOB_ACTIVE = "job_active"


class ReferenceFrameKind(enum.StrEnum):
    """How a calibration image was made."""

    #: The per-pixel median of frames across the video. Moving players vanish,
    #: so this is the one to calibrate on unless the camera moved.
    BACKGROUND = "background"
    #: One frame as it was decoded.
    SAMPLE = "sample"


class CalibrationSource(enum.StrEnum):
    """Who placed a calibration's landmarks."""

    MANUAL = "manual"


class ModelRunStage(enum.StrEnum):
    """Which half of player tracking a run is (ADR 0001)."""

    #: Person detection, court gate and ByteTrack over the video. Slow, and
    #: writes image-space ground-contact points only.
    DETECT = "detect"
    #: Player selection, partners, changeovers and court coordinates, read
    #: from one Detect run. Takes seconds, so every correction re-runs it.
    IDENTIFY = "identify"


class JobStage(enum.StrEnum):
    """Pipeline stage a job has reached.

    The names follow the stage sequence in `docs/ARCHITECTURE.md`. Jobs only
    reach `METADATA_READY` today, because reading the video is all the worker
    does; the later stages are listed so the column's meaning does not change
    when the real computer-vision stages are wired in.
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
    """One uploaded video of doubles play, and the owner of everything derived from it.

    A match is the whole upload, so it can hold several games.
    """

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
    #: The Identify run whose player tracks the match is showing. Every run is
    #: kept, so pointing here at a newer one replaces nothing.
    current_identify_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "model_runs.id",
            name="fk_matches_current_identify_run_id_model_runs",
            # `model_runs` also points at `matches`; the cycle means one of the
            # two constraints has to be added after both tables exist.
            use_alter=True,
        ),
        default=None,
    )

    video: Mapped["Video | None"] = relationship(
        back_populates="match", cascade="all, delete-orphan", uselist=False
    )
    jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="AnalysisJob.created_at, AnalysisJob.id",
    )

    reference_frames: Mapped[list["ReferenceFrame"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="ReferenceFrame.ordinal",
    )
    calibrations: Mapped[list["CourtCalibration"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="CourtCalibration.created_at, CourtCalibration.id",
    )

    runs: Mapped[list["ModelRun"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        foreign_keys="ModelRun.match_id",
        order_by="ModelRun.started_at, ModelRun.id",
    )
    current_identify_run: Mapped["ModelRun | None"] = relationship(
        foreign_keys=[current_identify_run_id],
        # Written after the run row exists, and cleared before it is deleted,
        # which is how the ORM gets through the cycle with `model_runs`.
        post_update=True,
    )

    @validates("current_identify_run")
    def _check_shown_run(self, _key: str, run: "ModelRun | None") -> "ModelRun | None":
        """Only an Identify run of this match. The foreign key proves no more than
        that the run exists, and a match pointing at another match's run would
        stop that match from ever being deleted."""
        if run is None:
            return None
        if run.stage is not ModelRunStage.IDENTIFY:
            raise ValueError(f"a match shows an Identify run, not a {run.stage.value} run")
        if run.match is not self:
            raise ValueError("a match shows only its own runs")
        return run

    @property
    def latest_calibration(self) -> "CourtCalibration | None":
        """The calibration in force. Earlier ones stay, so a recalibration
        never destroys what was there before it."""
        return self.calibrations[-1] if self.calibrations else None

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

    def reference_frame_job_refusal(self) -> ReferenceFrameJobRefusal | None:
        """Why a job for calibration images may not start, or None if it may.

        For a match whose metadata was extracted before calibration images
        existed, or whose attempt at them failed. A match with no metadata goes
        through `metadata_job_refusal` instead, because any job produces both.
        """
        if self.video is None:
            return ReferenceFrameJobRefusal.NO_VIDEO
        if any(job.status in ACTIVE_JOB_STATUSES for job in self.jobs):
            return ReferenceFrameJobRefusal.JOB_ACTIVE
        if self.video.metadata_extracted_at is None:
            return ReferenceFrameJobRefusal.NO_METADATA
        if self.reference_frames:
            return ReferenceFrameJobRefusal.ALREADY_EXTRACTED
        return None

    @property
    def can_extract_reference_frames(self) -> bool:
        return self.reference_frame_job_refusal() is None

    def calibration_refusal(self) -> CalibrationRefusal | None:
        """Why a calibration may not be saved, or None if it may."""
        if any(job.status in ACTIVE_JOB_STATUSES for job in self.jobs):
            return CalibrationRefusal.JOB_ACTIVE
        if not self.reference_frames:
            return CalibrationRefusal.NO_REFERENCE_FRAMES
        return None

    @property
    def can_calibrate(self) -> bool:
        return self.calibration_refusal() is None


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
    #: Each is `from_seconds`, `to_seconds` and `shift_px`. Null means not checked,
    #: and an empty list means checked and no move found.
    camera_jumps: Mapped[list[dict[str, float]] | None] = mapped_column(
        JSON(none_as_null=True), default=None
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


class ReferenceFrame(Base):
    """A still from the video, for placing calibration landmarks on.

    Generated by the worker, in display orientation, so a pixel on it is a pixel
    in every later image coordinate. Its size is stored because the size is what
    the browser needs to turn a click into a pixel.
    """

    __tablename__ = "reference_frames"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE", name="fk_reference_frames_match_id_matches")
    )
    #: Display order: the background first.
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[ReferenceFrameKind] = mapped_column(
        _enum_column(ReferenceFrameKind, "reference_frame_kind")
    )
    #: About where in the video a sample came from. Null for a background,
    #: which comes from no single moment.
    position_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    #: How many decoded frames went into this image.
    source_frame_count: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    match: Mapped[Match] = relationship(back_populates="reference_frames")

    __table_args__ = (
        UniqueConstraint("match_id", "ordinal", name="uq_reference_frames_match_id_ordinal"),
        CheckConstraint("width > 0", name="ck_reference_frames_width_positive"),
        CheckConstraint("height > 0", name="ck_reference_frames_height_positive"),
        CheckConstraint("byte_size >= 0", name="ck_reference_frames_byte_size_non_negative"),
        CheckConstraint(
            "source_frame_count > 0", name="ck_reference_frames_source_frame_count_positive"
        ),
        CheckConstraint(
            "(kind = 'background' AND position_seconds IS NULL)"
            " OR (kind = 'sample' AND position_seconds IS NOT NULL)",
            name="ck_reference_frames_position_matches_kind",
        ),
    )


class CourtCalibration(Base):
    """Landmarks placed on a reference frame, and the homography fitted to them.

    Never updated: a new calibration is a new row, and the newest is the one in
    force. `homography` maps image pixels to court feet.
    """

    __tablename__ = "court_calibrations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE", name="fk_court_calibrations_match_id_matches")
    )
    reference_frame_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "reference_frames.id",
            ondelete="CASCADE",
            name="fk_court_calibrations_reference_frame_id_reference_frames",
        )
    )
    source: Mapped[CalibrationSource] = mapped_column(
        _enum_column(CalibrationSource, "calibration_source"), default=CalibrationSource.MANUAL
    )
    #: Keyed by landmark name, as [x, y]: pixels, then feet.
    image_points: Mapped[dict[str, list[float]]] = mapped_column(JSON)
    court_points: Mapped[dict[str, list[float]]] = mapped_column(JSON)
    homography: Mapped[list[list[float]]] = mapped_column(JSON)
    #: Keyed by landmark name, in feet.
    reprojection_error_ft: Mapped[dict[str, float]] = mapped_column(JSON)
    mean_reprojection_error_ft: Mapped[float] = mapped_column(Float)
    max_reprojection_error_ft: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    match: Mapped[Match] = relationship(back_populates="calibrations")
    #: Declared so the ORM orders writes by it: a frame is inserted before its
    #: calibrations, and a calibration (with the runs that used it) is deleted
    #: before its frame, whose own deletion would otherwise be refused.
    reference_frame: Mapped[ReferenceFrame] = relationship()

    __table_args__ = (
        CheckConstraint(
            "mean_reprojection_error_ft >= 0", name="ck_court_calibrations_mean_error_non_negative"
        ),
        CheckConstraint(
            "max_reprojection_error_ft >= 0", name="ck_court_calibrations_max_error_non_negative"
        ),
        Index("ix_court_calibrations_match_id_created_at", "match_id", "created_at"),
    )


def hash_config(config: dict[str, Any], calibration_id: uuid.UUID) -> str:
    """The SHA-256 of a run's configuration together with the calibration it uses.

    The same whatever order the configuration's keys are in. Two runs with the
    same hash and model version were asked to do the same thing, which is how a
    later stage can tell whether a run can be reused. The calibration is part of
    the hash, so a run made under an old calibration never looks reusable after
    a recalibration. It is hashed from the `calibration_id` column, not copied
    into `config_json`, so it is stored in one place only.
    """
    payload = {"calibration_id": calibration_id.hex, "config": config}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ModelRun(Base):
    """One run of a model over a match, recorded so every prediction can be traced.

    Never updated after it completes and never deleted while its match exists:
    a recalibration or a correction is a new Identify run, and the original
    prediction survives it. The run's output is a Parquet artifact in `Storage`
    (see `pickleball_api.run_artifacts`), not rows in the database.
    """

    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE", name="fk_model_runs_match_id_matches")
    )
    stage: Mapped[ModelRunStage] = mapped_column(_enum_column(ModelRunStage, "model_run_stage"))
    model_version: Mapped[str] = mapped_column(String(128))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    #: `hash_config(config_json, calibration_id)`, as hex.
    config_hash: Mapped[str] = mapped_column(String(64))
    #: The calibration the run used: Detect gates people with it before
    #: ByteTrack, and Identify projects into court feet with it (ADR 0001).
    #: Not cascaded, so a calibration a run used cannot be deleted on its own.
    calibration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("court_calibrations.id", name="fk_model_runs_calibration_id_court_calibrations")
    )
    #: Null until the run has written its artifact.
    artifact_storage_key: Mapped[str | None] = mapped_column(String(512), unique=True, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Null while the run is in progress, or if it never finished.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: The run whose output this one read: an Identify run's Detect run.
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_runs.id", name="fk_model_runs_parent_run_id_model_runs"),
        default=None,
    )

    match: Mapped[Match] = relationship(back_populates="runs", foreign_keys=[match_id])
    parent_run: Mapped["ModelRun | None"] = relationship(remote_side=[id])
    calibration: Mapped[CourtCalibration] = relationship()

    __table_args__ = (
        CheckConstraint(
            "(stage = 'detect' AND parent_run_id IS NULL)"
            " OR (stage = 'identify' AND parent_run_id IS NOT NULL)",
            name="ck_model_runs_parent_matches_stage",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_model_runs_completed_after_started",
        ),
        Index("ix_model_runs_match_id_started_at", "match_id", "started_at"),
    )

    @classmethod
    def start(
        cls,
        match: Match,
        stage: ModelRunStage,
        *,
        calibration: CourtCalibration,
        model_version: str,
        config: dict[str, Any],
        parent: "ModelRun | None" = None,
    ) -> "ModelRun":
        """A new, unfinished run of `match`, with its configuration hashed.

        Both stages use a calibration of the same match, which must already be
        saved, because its id is hashed. An Identify run reads a Detect run of
        the same match, and a Detect run reads only the video. Anything else is
        refused here rather than left for the database to half-catch.
        """
        if calibration.match is not match:
            raise ValueError("a run uses a calibration of its own match")
        if calibration.id is None:
            raise ValueError("a run uses a saved calibration")
        if stage is ModelRunStage.IDENTIFY:
            if parent is None or parent.stage is not ModelRunStage.DETECT:
                raise ValueError("an Identify run reads a Detect run")
            if parent.match is not match:
                raise ValueError("an Identify run reads a Detect run of its own match")
        elif parent is not None:
            raise ValueError(f"a {stage.value} run reads no other run")
        run = cls(
            stage=stage,
            model_version=model_version,
            config_json=config,
            config_hash=hash_config(config, calibration.id),
            calibration=calibration,
            parent_run=parent,
            started_at=utcnow(),
        )
        # Through the collection rather than `run.match`: only this side
        # cascades the new run into the session of a match that is already in one.
        match.runs.append(run)
        return run


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
