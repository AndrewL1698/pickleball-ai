"""`pbworker-backfill-playback`: make playback copies for videos that have none.

    uv run pbworker-backfill-playback

A video processed before playback copies existed already has its metadata, so
its match never runs a preparation job again. This makes the missing copies
inline, one video at a time, oldest first, saving each as it finishes. Running
it again skips the videos already done, which is also how a failed transcode is
retried.

It queues no jobs: an active job on a match blocks its calibration. A
preparation job that saves a copy while this is encoding wins; this deletes
its own copy rather than overwrite that one.

Logs one line per video and a summary, and exits 1 if any copy failed.
"""

import argparse
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from pickleball_api.config import get_settings
from pickleball_api.db import session_scope
from pickleball_api.models import Video
from pickleball_api.storage import LocalFileStorage, Storage
from pickleball_worker.playback import PlaybackCopyFailed, make_playback_copy

logger = logging.getLogger("pickleball_worker.backfill_playback")


@dataclass(frozen=True)
class BackfillSummary:
    """What one run did."""

    made: int = 0
    failed: int = 0
    #: A copy was saved by something else while this one was encoding.
    already_had_one: int = 0


@dataclass(frozen=True)
class _Pending:
    id: UUID
    storage_key: str
    width: int
    height: int
    duration_seconds: float


def backfill(storage: Storage) -> BackfillSummary:
    """Make and save a copy for every video with metadata and no copy."""
    made = failed = already_had_one = 0
    pending = _pending()
    logger.info("%d video(s) without a playback copy", len(pending))
    for video in pending:
        started = time.monotonic()
        try:
            key = make_playback_copy(
                storage,
                video.storage_key,
                width=video.width,
                height=video.height,
                duration_seconds=video.duration_seconds,
            )
        except PlaybackCopyFailed as exc:
            failed += 1
            logger.warning("video %s: failed: %s", video.id, exc)
            continue
        try:
            saved = _save(video.id, key)
        except BaseException:
            storage.delete(key)
            raise
        if saved:
            made += 1
            logger.info("video %s: copy made in %.1f s", video.id, time.monotonic() - started)
        else:
            already_had_one += 1
            storage.delete(key)
            logger.info("video %s: skipped, a copy was saved meanwhile", video.id)
    summary = BackfillSummary(made=made, failed=failed, already_had_one=already_had_one)
    logger.info(
        "done: %d made, %d failed, %d already had one",
        summary.made,
        summary.failed,
        summary.already_had_one,
    )
    return summary


def _pending() -> list[_Pending]:
    with session_scope() as session:
        rows = session.execute(
            select(
                Video.id, Video.storage_key, Video.width, Video.height, Video.duration_seconds
            )
            .where(Video.metadata_extracted_at.is_not(None))
            .where(Video.playback_storage_key.is_(None))
            .order_by(Video.created_at, Video.id)
        ).all()
    # `ck_videos_metadata_complete` makes these non-null once metadata is extracted.
    return [
        _Pending(row.id, row.storage_key, row.width, row.height, row.duration_seconds)
        for row in rows
        if row.width is not None and row.height is not None and row.duration_seconds is not None
    ]


def _save(video_id: UUID, key: str) -> bool:
    """Record the copy, unless the video is gone or got one meanwhile.

    The row is locked while it is checked, so a preparation job saving its own
    copy at the same moment waits, and then keeps ours rather than overwrite it.
    """
    with session_scope() as session:
        video = session.get(Video, video_id, with_for_update=True)
        if video is None or video.playback_storage_key is not None:
            return False
        video.playback_storage_key = key
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pbworker-backfill-playback",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    storage = LocalFileStorage(get_settings().upload_dir)
    logger.info("reading uploads from %s", storage.root)
    return 1 if backfill(storage).failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
