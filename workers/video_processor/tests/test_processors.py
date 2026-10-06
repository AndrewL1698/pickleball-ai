"""The metadata processor on its own, with the decoder mocked at its boundary.

`read_metadata` is replaced where the processor looks it up
(`pickleball_worker.processors.read_metadata`), so these tests need no video
file and no database. The real decode is covered in `test_tasks.py`.
"""

import uuid
from pathlib import Path

import pytest

from pickleball_api.errors import JobErrorCode, JobFailure
from pickleball_api.models import JobStage
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_ml.video.reader import InvalidVideoMetadata, VideoMetadata, VideoReadError
from pickleball_worker import processors
from pickleball_worker.processors import MetadataProcessor, ProcessingResult, VideoRef

CONTENT = b"stand-in bytes; the decoder is mocked"


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "uploads")


@pytest.fixture
def ref(storage: LocalFileStorage) -> VideoRef:
    key = new_storage_key(".mp4")
    storage.write(key, [CONTENT], max_bytes=len(CONTENT))
    return VideoRef(
        id=uuid.uuid4(),
        match_id=uuid.uuid4(),
        storage_key=key,
        original_filename="m.mp4",
        content_type="video/mp4",
        byte_size=len(CONTENT),
    )


def decoded(**overrides: object) -> VideoMetadata:
    values: dict[str, object] = {
        "path": "/somewhere/secret.mp4",
        "width": 1080,
        "height": 1920,
        "fps": 29.97,
        "frame_count": 300,
        "duration_seconds": 300 / 29.97,
        "codec": "hvc1",
        "rotation_degrees": 90,
    }
    values.update(overrides)
    return VideoMetadata(**values)  # type: ignore[arg-type]


def run(ref: VideoRef, storage: LocalFileStorage) -> ProcessingResult:
    return MetadataProcessor().process(ref, storage, lambda stage, progress: None)


def test_the_decoded_metadata_is_returned_as_a_typed_result(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def fake_read(path: Path) -> VideoMetadata:
        seen.append(path)
        return decoded()

    monkeypatch.setattr(processors, "read_metadata", fake_read)
    result = run(ref, storage)

    assert isinstance(result, ProcessingResult)
    m = result.metadata
    assert (m.width, m.height, m.rotation_degrees) == (1080, 1920, 90)
    assert m.average_fps == pytest.approx(29.97)
    assert (m.frame_count, m.codec) == (300, "hvc1")
    assert m.duration_seconds == pytest.approx(300 / 29.97)
    # The decoder was given the stored file itself, not a copy.
    assert seen == [storage.path_for(ref.storage_key)]
    # Nothing about where the file lives leaks into the result.
    assert "secret" not in repr(result) and ref.storage_key not in repr(result)


def test_an_unnamed_codec_becomes_none_not_an_empty_string(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processors, "read_metadata", lambda path: decoded(codec=""))
    assert run(ref, storage).metadata.codec is None


@pytest.mark.parametrize(
    "error",
    [
        VideoReadError("could not open video /abs/path/key.mp4"),
        InvalidVideoMetadata("implausible frame rate 0.0"),
    ],
)
def test_a_decode_failure_is_the_safe_unreadable_code(
    ref: VideoRef,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def fail(path: Path) -> VideoMetadata:
        raise error

    monkeypatch.setattr(processors, "read_metadata", fail)
    with pytest.raises(JobFailure) as caught:
        run(ref, storage)
    assert caught.value.code is JobErrorCode.UNREADABLE_VIDEO


def test_a_missing_storage_object_is_the_missing_file_code(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(processors, "read_metadata", lambda path: calls.append(path))
    storage.delete(ref.storage_key)
    with pytest.raises(JobFailure) as caught:
        run(ref, storage)
    assert caught.value.code is JobErrorCode.MISSING_VIDEO_FILE
    assert calls == []  # nothing was handed to the decoder


def test_a_file_that_vanishes_during_the_decode_is_the_missing_file_code(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vanish(path: Path) -> VideoMetadata:
        raise FileNotFoundError(path)

    monkeypatch.setattr(processors, "read_metadata", vanish)
    with pytest.raises(JobFailure) as caught:
        run(ref, storage)
    assert caught.value.code is JobErrorCode.MISSING_VIDEO_FILE


def test_a_stored_object_of_the_wrong_size_is_not_decoded(
    storage: LocalFileStorage, ref: VideoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated or replaced file might decode as a different video."""
    calls: list[Path] = []
    monkeypatch.setattr(processors, "read_metadata", lambda path: calls.append(path))
    wrong = VideoRef(**{**ref.__dict__, "byte_size": ref.byte_size + 1})
    with pytest.raises(JobFailure) as caught:
        run(wrong, storage)
    assert caught.value.code is JobErrorCode.UNREADABLE_VIDEO
    assert calls == []


def test_an_unexpected_error_is_not_disguised_as_a_bad_video(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug is not an unreadable video. It propagates, and the task layer
    records it as an internal error with the traceback in the log."""

    def bug(path: Path) -> VideoMetadata:
        raise KeyError("oops")

    monkeypatch.setattr(processors, "read_metadata", bug)
    with pytest.raises(KeyError):
        run(ref, storage)


def test_progress_is_reported_and_never_claims_the_terminal_stage(
    ref: VideoRef, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`metadata_ready` is set by the task layer once the metadata is saved,
    not by the processor before anything has been persisted."""
    monkeypatch.setattr(processors, "read_metadata", lambda path: decoded())
    reports: list[tuple[JobStage, float]] = []
    MetadataProcessor().process(ref, storage, lambda s, p: reports.append((s, p)))
    assert reports[0] == (JobStage.INGESTED, 0.0)
    assert all(stage is JobStage.INGESTED for stage, _ in reports)
    assert [p for _, p in reports] == sorted(p for _, p in reports)


def test_the_worker_does_not_import_torch_or_ultralytics() -> None:
    """Only the lightweight video module: no model libraries in this process."""
    import subprocess
    import sys

    code = (
        "import sys, pickleball_worker.tasks, pickleball_worker.processors;"
        "heavy = [m for m in ('torch', 'ultralytics') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert output == ""
