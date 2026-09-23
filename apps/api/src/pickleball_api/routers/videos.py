"""Uploading a video and reading back what happened to it."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from pickleball_api import jobs, uploads
from pickleball_api.config import MEGABYTE
from pickleball_api.dependencies import QueueDep, SessionDep, SettingsDep, StorageDep
from pickleball_api.errors import JobErrorCode
from pickleball_api.limits import content_length_of, too_large_message
from pickleball_api.models import AnalysisJob, JobStage, JobStatus, Video
from pickleball_api.queue import QueueUnavailable
from pickleball_api.schemas import VideoDetail, VideoList, VideoSummary
from pickleball_api.storage import (
    ObjectTooLarge,
    Storage,
    discard_on_error,
    new_storage_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])

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


@router.post("", response_model=VideoDetail, status_code=status.HTTP_201_CREATED)
def upload_video(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File(description="An mp4, m4v, or mov video")],
) -> VideoDetail:
    """Accept a video, record it, and queue it for analysis.

    The order matters and is the whole point of this function:

    1. Validate, so nothing unwanted is written at all.
    2. Write the bytes under a key this process generated.
    3. Insert both rows and commit; if that fails, delete the orphaned file.
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
        video = Video(
            original_filename=filename,
            storage_key=storage_key,
            content_type=uploads.content_type_for(extension),
            byte_size=byte_size,
        )
        job = AnalysisJob(
            video=video, status=JobStatus.QUEUED, stage=JobStage.INGESTED, progress=0.0
        )
        session.add_all([video, job])
        session.commit()

    if session.in_transaction():  # pragma: no cover - guards an ordering mistake
        raise RuntimeError("the job must be committed before it is enqueued")
    try:
        queue.enqueue(job.id)
    except QueueUnavailable:
        # The rows stay: the upload really did happen, and the job can be
        # queued again once Redis is back. It is marked failed rather than left
        # sitting in `queued`, which would claim a worker has it.
        jobs.transition(job, JobStatus.FAILED, error_code=JobErrorCode.ENQUEUE_FAILED)
        session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The video was stored but could not be queued for processing.",
        ) from None

    # No refresh before serializing: the session does not expire on commit, and
    # `id` and `created_at` are Python-side defaults, so every field the
    # response reads is already populated. Refreshing cost two more queries --
    # the reload, plus a lazy load of `jobs` that the refresh had expired.
    return VideoDetail.of(video)


@router.get("", response_model=VideoList)
def list_videos(session: SessionDep, limit: int = 100, offset: int = 0) -> VideoList:
    """Videos newest first, each with the status of its most recent job."""
    videos = jobs.list_videos(session, limit=min(max(limit, 1), 200), offset=max(offset, 0))
    return VideoList(videos=[VideoSummary.of(v) for v in videos], count=len(videos))


@router.get("/{video_id}", response_model=VideoDetail)
def read_video(session: SessionDep, video_id: UUID) -> VideoDetail:
    video = jobs.get_video(session, video_id)
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such video.")
    return VideoDetail.of(video)
