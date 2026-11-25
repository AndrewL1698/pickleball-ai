"""Creating a match by uploading its video, and reading matches back.

These are the primary resources. Phase 1's `/api/videos` endpoints were removed
rather than kept as aliases: a video is now something a match owns, and two
collections describing the same uploads would drift apart.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pickleball_api import jobs, uploads
from pickleball_api.calibration import CalibrationRejected, fit_calibration
from pickleball_api.config import MEGABYTE, Settings
from pickleball_api.dependencies import QueueDep, SessionDep, SettingsDep, StorageDep
from pickleball_api.errors import JobErrorCode
from pickleball_api.limits import content_length_of, too_large_message
from pickleball_api.models import (
    AnalysisJob,
    CalibrationRefusal,
    CourtCalibration,
    JobKind,
    JobStage,
    JobStatus,
    Match,
    MatchStatus,
    MetadataJobRefusal,
    PlayersJobRefusal,
    ReferenceFrame,
    ReferenceFrameJobRefusal,
    Video,
)
from pickleball_api.queue import JobQueue, QueueUnavailable
from pickleball_api.schemas import CalibrationCreate, MatchDetail, MatchList, MatchSummary
from pickleball_api.storage import (
    ObjectNotFound,
    ObjectTooLarge,
    Storage,
    discard_on_error,
    new_storage_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/matches", tags=["matches"])

#: The sentence for each reason a metadata job is refused. Fixed text, so no
#: internal detail can reach a response through it.
REFUSAL_MESSAGES: dict[MetadataJobRefusal, str] = {
    MetadataJobRefusal.NO_VIDEO: "This match has no video to process.",
    MetadataJobRefusal.JOB_ACTIVE: "A processing job for this match is already queued or running.",
    MetadataJobRefusal.ALREADY_EXTRACTED: "This match's video metadata has already been extracted.",
}

REFERENCE_FRAME_REFUSAL_MESSAGES: dict[ReferenceFrameJobRefusal, str] = {
    ReferenceFrameJobRefusal.NO_VIDEO: REFUSAL_MESSAGES[MetadataJobRefusal.NO_VIDEO],
    ReferenceFrameJobRefusal.NO_METADATA: "This match's video metadata has not been extracted yet.",
    ReferenceFrameJobRefusal.JOB_ACTIVE: REFUSAL_MESSAGES[MetadataJobRefusal.JOB_ACTIVE],
    ReferenceFrameJobRefusal.ALREADY_EXTRACTED: "This match already has calibration images.",
}

CALIBRATION_REFUSAL_MESSAGES: dict[CalibrationRefusal, str] = {
    CalibrationRefusal.NO_REFERENCE_FRAMES: "This match has no calibration images yet.",
    CalibrationRefusal.JOB_ACTIVE: REFUSAL_MESSAGES[MetadataJobRefusal.JOB_ACTIVE],
}

PLAYERS_JOB_REFUSAL_MESSAGES: dict[PlayersJobRefusal, str] = {
    PlayersJobRefusal.NO_CALIBRATION: "This match's court has not been calibrated yet.",
    PlayersJobRefusal.JOB_ACTIVE: REFUSAL_MESSAGES[MetadataJobRefusal.JOB_ACTIVE],
    PlayersJobRefusal.ALREADY_DETECTED: "This match's players have already been detected.",
}

#: A frame is written once and never changes, and its id is random.
REFERENCE_FRAME_HEADERS = {
    "Cache-Control": "private, max-age=86400, immutable",
    # The type below is fixed, so nothing may second-guess it, and nothing
    # opened from this response may run.
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Content-Disposition": 'inline; filename="reference-frame.jpg"',
}

#: A playback copy is written once under a random key and never changes, so it
#: is cached and protected exactly like a reference frame.
PLAYBACK_HEADERS = {
    **REFERENCE_FRAME_HEADERS,
    "Content-Disposition": 'inline; filename="playback.mp4"',
}

#: Slack left free after an upload, so the disk does not end up at exactly 0.
STORAGE_HEADROOM_BYTES = 256 * MEGABYTE


def _require_room(storage: Storage, declared: int | None, limit: int) -> None:
    """Refuse an upload the disk cannot hold.

    A match is often several gigabytes, so filling the disk is an ordinary
    Tuesday rather than an attack. The check is sized to this request where the
    client declared a length: sizing it to the configured maximum instead would
    refuse a 4 KB upload whenever free space fell below the 2 GiB limit.
    """
    free = storage.free_bytes()
    if free is None:
        return
    needed = min(declared, limit) if declared else limit
    if free < needed + STORAGE_HEADROOM_BYTES:
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="There is not enough free space to accept this upload.",
        )


@router.post("", response_model=MatchDetail, status_code=status.HTTP_201_CREATED)
def create_match(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File(description="An mp4, m4v, or mov video")],
) -> MatchDetail:
    """Accept a match's video, record the match, and queue it for analysis.

    The order matters and is the whole point of this function:

    1. Validate, so nothing unwanted is written at all.
    2. Write the bytes under a key this process generated.
    3. Insert the match, its video and its job in one transaction and commit;
       if that fails, delete the orphaned file.
    4. Only then enqueue. Enqueueing inside the transaction would let a worker
       read the job before the commit is visible and conclude it does not exist.

    The route is synchronous on purpose. Every collaborator it uses -- the
    session, the storage, the queue -- is synchronous, so FastAPI runs it in a
    worker thread and a multi-gigabyte upload never blocks the event loop.
    """
    try:
        filename = uploads.clean_filename(file.filename)
        extension = uploads.video_extension(filename, settings.allowed_video_extensions)
    except uploads.UploadRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=exc.detail) from exc
    _require_room(storage, content_length_of(request), settings.max_upload_bytes)

    storage_key = new_storage_key(extension)
    try:
        byte_size = storage.write(
            storage_key, uploads.read_chunks(file.file), max_bytes=settings.max_upload_bytes
        )
    except ObjectTooLarge as exc:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail=too_large_message(settings.max_upload_bytes),
        ) from exc
    except uploads.UploadRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=exc.detail) from exc

    with discard_on_error(storage, storage_key):
        match = Match(name=uploads.default_match_name(filename), status=MatchStatus.UPLOADED)
        match.video = Video(
            original_filename=filename,
            storage_key=storage_key,
            content_type=uploads.content_type_for(extension),
            byte_size=byte_size,
        )
        job = AnalysisJob(
            match=match, status=JobStatus.QUEUED, stage=JobStage.INGESTED, progress=0.0
        )
        session.add(match)  # the video and the job cascade from it
        session.commit()

    _enqueue_committed(
        session,
        queue,
        settings,
        job,
        failure="The match was created and its video stored, but it could not be queued "
        "for processing.",
    )

    # No refresh before serializing: the session does not expire on commit, and
    # `id` and `created_at` are Python-side defaults, so every field the
    # response reads is already populated. Refreshing cost two more queries --
    # the reload, plus lazy loads of the relationships the refresh had expired.
    return MatchDetail.of(match)


@router.post(
    "/{match_id}/metadata-jobs",
    response_model=MatchDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "Metadata already extracted, or a job is active"}},
)
def start_metadata_job(
    session: SessionDep, queue: QueueDep, settings: SettingsDep, match_id: UUID
) -> MatchDetail:
    """Queue a metadata extraction job for a match that has no metadata.

    For a match whose last attempt failed, and for every match that predates
    metadata extraction. Refused with `409` when the metadata already exists or
    a job is already queued or running. Earlier jobs are kept; this adds one.

    Concurrency: the match row is locked while the rule is checked, so two
    requests for the same match take turns on PostgreSQL. Should anything get
    past that, the partial unique index on `analysis_jobs` refuses a second
    active job at commit, and that too is answered `409`.
    """
    match = _lock_match(session, match_id)
    refusal = match.metadata_job_refusal()
    if refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=REFUSAL_MESSAGES[refusal])
    return _start_job(session, queue, settings, match)


@router.post(
    "/{match_id}/reference-frame-jobs",
    response_model=MatchDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "No metadata yet, images already exist, or a job is active"}},
)
def start_reference_frame_job(
    session: SessionDep, queue: QueueDep, settings: SettingsDep, match_id: UUID
) -> MatchDetail:
    """Queue a job to make calibration images for a match that has none.

    For a match extracted before calibration images existed, or whose attempt at
    them failed. It is the same job an upload gets, so nothing is enqueued from
    a migration. Concurrency works as it does for `start_metadata_job`.
    """
    match = _lock_match(session, match_id)
    refusal = match.reference_frame_job_refusal()
    if refusal is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=REFERENCE_FRAME_REFUSAL_MESSAGES[refusal]
        )
    return _start_job(session, queue, settings, match)


@router.post(
    "/{match_id}/players-jobs",
    response_model=MatchDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "Not calibrated, a job is active, or Detect has finished"}},
)
def start_players_job(
    session: SessionDep, queue: QueueDep, settings: SettingsDep, match_id: UUID
) -> MatchDetail:
    """Queue player tracking for a calibrated match whose Detect has not finished.

    Saving a calibration queues the first players job by itself. This is for a
    match whose players job failed or could not be queued: the new job continues
    the match's unfinished Detect run, with that run's own calibration and
    configuration, from its first unfinished chunk. Refused with `409` without a
    calibration, while any job is active, or once Detect has finished, since a
    recalibration never re-detects (ADR 0001). Concurrency works as it does for
    `start_metadata_job`.
    """
    match = _lock_match(session, match_id)
    refusal = match.players_job_refusal()
    if refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=PLAYERS_JOB_REFUSAL_MESSAGES[refusal])
    return _start_job(session, queue, settings, match, JobKind.PLAYERS)


@router.get(
    "/{match_id}/reference-frames/{frame_id}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def read_reference_frame(
    session: SessionDep, storage: StorageDep, match_id: UUID, frame_id: UUID
) -> Response:
    """One calibration image.

    The worker generated these bytes, so unlike the upload they cannot be a file
    a visitor chose. The small size means reading it whole does no harm.
    """
    frame = session.get(ReferenceFrame, frame_id)
    if frame is None or frame.match_id != match_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such calibration image.")
    try:
        with storage.open(frame.storage_key) as stored:
            content = stored.read()
    except ObjectNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="This calibration image is no longer in storage."
        ) from exc
    return Response(content=content, media_type="image/jpeg", headers=REFERENCE_FRAME_HEADERS)


@router.get(
    "/{match_id}/playback",
    response_class=FileResponse,
    responses={
        200: {"content": {"video/mp4": {}}},
        206: {"description": "The requested byte range", "content": {"video/mp4": {}}},
        404: {"description": "No such match, or no playback copy yet"},
        416: {"description": "The byte range is past the end of the copy"},
    },
)
def read_playback(session: SessionDep, storage: StorageDep, match_id: UUID) -> FileResponse:
    """The match's H.264 playback copy, for a browser `<video>` to play and seek.

    `Range` requests get `206 Partial Content`, which is how a browser seeks
    without downloading the whole file; Starlette's `FileResponse` handles them,
    including `416` for a range past the end. `404` while there is no copy:
    `playback_available` on the match's video says when there is one.
    """
    match = session.get(Match, match_id)
    video = None if match is None else match.video
    key = None if video is None else video.playback_storage_key
    if key is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="This match has no playback copy yet."
        )
    try:
        # `local_path` promises the path only inside the block, and FileResponse
        # reads it after this returns. That is safe for `LocalFileStorage`, whose
        # path is the stored file itself. An object store would answer with a
        # redirect to a signed URL here instead.
        with storage.local_path(key) as path:
            pass
    except ObjectNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="This match's playback copy is no longer in storage."
        ) from exc
    return FileResponse(path, media_type="video/mp4", headers=PLAYBACK_HEADERS)


@router.post(
    "/{match_id}/calibrations",
    response_model=MatchDetail,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "No calibration images yet, or a job is active"},
        422: {"description": "The landmarks cannot produce a usable calibration"},
    },
)
def create_calibration(
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
    match_id: UUID,
    body: CalibrationCreate,
) -> MatchDetail:
    """Fit a homography to landmarks placed on a reference frame, and save it.

    A homography over a dozen points takes no meaningful time, so this runs in
    the request and the answer comes straight back. An earlier calibration is
    kept: the newest is the one in force.

    The first calibration of a match queues its players job, committed with the
    calibration. Nothing else is queued: not while a job is active, and never
    once Detect has run, finished or not (ADR 0001). A queue that is down fails
    that job, but the calibration still stands and the answer is still `201`:
    `can_start_players_job` then offers the retry.
    """
    match = _lock_match(session, match_id)
    refusal = match.calibration_refusal()
    if refusal is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=CALIBRATION_REFUSAL_MESSAGES[refusal]
        )
    frame = next((f for f in match.reference_frames if f.id == body.reference_frame_id), None)
    if frame is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That calibration image does not belong to this match.",
        )
    try:
        fitted = fit_calibration(
            {landmark.value: point for landmark, point in body.image_points.items()},
            width=frame.width,
            height=frame.height,
        )
    except CalibrationRejected as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.detail
        ) from exc

    match.calibrations.append(
        CourtCalibration(
            reference_frame_id=frame.id,
            image_points=fitted.image_points,
            court_points=fitted.court_points,
            homography=fitted.homography,
            reprojection_error_ft=fitted.reprojection_error_ft,
            mean_reprojection_error_ft=fitted.mean_reprojection_error_ft,
            max_reprojection_error_ft=fitted.max_reprojection_error_ft,
        )
    )
    match.status = MatchStatus.COURT_READY
    job = None
    if match.calibration_starts_players_job:
        job = jobs.new_job(match, JobKind.PLAYERS)
        # SQLAlchemy 2 does not cascade a new object into the session through a backref.
        session.add(job)
    session.commit()
    if job is not None:
        _try_enqueue(session, queue, settings, job)
    return MatchDetail.of(match)


def _lock_match(session: Session, match_id: UUID) -> Match:
    """The match, locked until commit, so requests for it take turns on PostgreSQL."""
    match = session.scalar(select(Match).where(Match.id == match_id).with_for_update())
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such match.")
    return match


def _start_job(
    session: Session,
    queue: JobQueue,
    settings: Settings,
    match: Match,
    kind: JobKind = JobKind.PREPARATION,
) -> MatchDetail:
    """Commit a new job for a match that has been checked, then queue it."""
    job = jobs.new_job(match, kind)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=REFUSAL_MESSAGES[MetadataJobRefusal.JOB_ACTIVE],
        ) from None

    _enqueue_committed(
        session,
        queue,
        settings,
        job,
        failure="The job was recorded but could not be queued for processing.",
    )
    return MatchDetail.of(match)


def _enqueue_committed(
    session: Session, queue: JobQueue, settings: Settings, job: AnalysisJob, *, failure: str
) -> None:
    """Hand a committed job to the worker, or fail it visibly.

    Enqueueing inside the transaction would let a worker read the job before
    the commit is visible and conclude it does not exist. If the queue is down
    the rows stay -- the request really did happen -- but the job is marked
    failed rather than left in `queued`, which would claim a worker has it, and
    the transition fails the match with it. The response is a fixed sentence.
    """
    if not _try_enqueue(session, queue, settings, job):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=failure)


def _try_enqueue(session: Session, queue: JobQueue, settings: Settings, job: AnalysisJob) -> bool:
    """Hand a committed job to the worker, or fail it with `enqueue_failed`.

    A players job gets its own, longer time limit: tracking runs about as long
    as the match.
    """
    if session.in_transaction():  # pragma: no cover - guards an ordering mistake
        raise RuntimeError("the job must be committed before it is enqueued")
    try:
        if job.kind is JobKind.PLAYERS:
            queue.enqueue(job.id, timeout_seconds=settings.players_job_timeout_seconds)
        else:
            queue.enqueue(job.id)
    except QueueUnavailable:
        jobs.transition(job, JobStatus.FAILED, error_code=JobErrorCode.ENQUEUE_FAILED)
        session.commit()
        return False
    return True


@router.get("", response_model=MatchList)
def list_matches(session: SessionDep, limit: int = 100, offset: int = 0) -> MatchList:
    """Matches newest first, each with its video and its most recent job."""
    matches = jobs.list_matches(session, limit=min(max(limit, 1), 200), offset=max(offset, 0))
    return MatchList(matches=[MatchSummary.of(m) for m in matches], count=len(matches))


@router.get("/{match_id}", response_model=MatchDetail)
def read_match(session: SessionDep, match_id: UUID) -> MatchDetail:
    """One match, its video, its latest job, and every earlier attempt."""
    match = jobs.get_match(session, match_id)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such match.")
    return MatchDetail.of(match)
