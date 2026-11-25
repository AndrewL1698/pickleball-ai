"""`pbworker-backfill-playback`: copies for videos processed before copies existed."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_playback import probe

from pickleball_api import db
from pickleball_api.config import Settings
from pickleball_api.models import AnalysisJob, Match, Video, utcnow
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_worker import backfill_playback, playback
from pickleball_worker.backfill_playback import BackfillSummary, backfill, main


def give_metadata(video_id: object) -> None:
    """What a preparation job from before this change left: metadata, no copy."""
    with db.session_scope() as session:
        video = session.get(Video, video_id)
        assert video is not None
        video.width, video.height, video.rotation_degrees = 48, 64, 90
        video.average_fps, video.duration_seconds, video.frame_count = 10.0, 2.0, 20
        video.metadata_extracted_at = utcnow()


def copy_of(video_id: object) -> str | None:
    with db.session_scope() as session:
        video = session.get(Video, video_id)
        assert video is not None
        return video.playback_storage_key


@pytest.fixture
def processed(stored_video: tuple[Video, AnalysisJob]) -> Video:
    video, _ = stored_video
    give_metadata(video.id)
    return video


def test_a_video_with_metadata_gets_a_copy(
    processed: Video, storage: LocalFileStorage
) -> None:
    assert backfill(storage) == BackfillSummary(made=1)

    key = copy_of(processed.id)
    assert key is not None
    with storage.local_path(key) as path:
        copy = probe(path)
    assert copy.codec == "h264"
    assert (copy.width, copy.height, copy.frame_count) == (48, 64, 20)


def test_a_second_run_skips_a_video_already_done(
    processed: Video, storage: LocalFileStorage
) -> None:
    backfill(storage)
    first = copy_of(processed.id)
    files = sorted(storage.root.iterdir())

    assert backfill(storage) == BackfillSummary()
    assert copy_of(processed.id) == first
    assert sorted(storage.root.iterdir()) == files


def test_a_video_without_metadata_is_left_for_its_job(
    stored_video: tuple[Video, AnalysisJob], storage: LocalFileStorage
) -> None:
    video, _ = stored_video
    assert backfill(storage) == BackfillSummary()
    assert copy_of(video.id) is None


def test_a_copy_saved_meanwhile_is_never_overwritten(
    processed: Video, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preparation job saved one while the backfill was encoding: its copy stays."""
    existing = new_storage_key(".mp4")
    storage.write(existing, [b"mp4"], max_bytes=3)
    made: list[str] = []
    real = backfill_playback.make_playback_copy

    def racing(*args: object, **kwargs: object) -> str:
        key = real(*args, **kwargs)  # type: ignore[arg-type]
        made.append(key)
        with db.session_scope() as session:
            video = session.get(Video, processed.id)
            assert video is not None
            video.playback_storage_key = existing
        return key

    monkeypatch.setattr(backfill_playback, "make_playback_copy", racing)
    assert backfill(storage) == BackfillSummary(already_had_one=1)
    assert copy_of(processed.id) == existing
    assert storage.exists(existing)
    assert made and not storage.exists(made[0])


def test_a_failure_is_counted_and_the_others_still_get_copies(
    processed: Video, storage: LocalFileStorage, engine: Engine
) -> None:
    """A video whose file is gone fails; the next one is still copied."""
    match = Match(name="gone")
    gone = Video(
        match=match,
        original_filename="gone.mp4",
        storage_key=new_storage_key(".mp4"),
        content_type="video/mp4",
        byte_size=1,
        # Older than the fixture's video, so it is tried first.
        created_at=utcnow() - timedelta(days=1),
    )
    with db.session_scope() as session:
        session.add(match)
    give_metadata(gone.id)

    assert backfill(storage) == BackfillSummary(made=1, failed=1)
    assert copy_of(gone.id) is None
    assert copy_of(processed.id) is not None


def settings_for(storage: LocalFileStorage) -> Settings:
    return Settings(environment="test", upload_dir=storage.root)


def test_the_command_succeeds_when_every_copy_is_made(
    processed: Video, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backfill_playback, "get_settings", lambda: settings_for(storage))
    assert main([]) == 0
    assert copy_of(processed.id) is not None


def test_the_command_fails_when_a_copy_could_not_be_made(
    processed: Video,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(backfill_playback, "get_settings", lambda: settings_for(storage))
    monkeypatch.setattr(playback, "ffmpeg_exe", lambda: str(tmp_path / "no-such-ffmpeg"))
    assert main([]) == 1
    assert copy_of(processed.id) is None
