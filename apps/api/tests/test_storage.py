"""Storage keys, containment, and the size limit."""

import os
from pathlib import Path

import pytest

from pickleball_api.storage import (
    InvalidStorageKey,
    LocalFileStorage,
    ObjectNotFound,
    ObjectTooLarge,
    discard_on_error,
    new_storage_key,
)


def test_generated_keys_are_random_and_keep_the_extension() -> None:
    first, second = new_storage_key(".mp4"), new_storage_key(".mp4")
    assert first != second
    assert first.endswith(".mp4")
    assert len(first) == len("0" * 32 + ".mp4")


def test_generated_key_normalizes_a_leading_dot_and_case() -> None:
    assert new_storage_key("MOV").endswith(".mov")


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "..",
        ".",
        "nested/file.mp4",
        "nested\\file.mp4",
        "file.mp4\x00.txt",
        "",
        "not-hex.mp4",
        "a" * 32 + ".mp4/../../escape.mp4",
    ],
)
def test_paths_outside_the_store_are_refused(tmp_path: Path, key: str) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    with pytest.raises(InvalidStorageKey):
        storage.path_for(key)


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    written = storage.write(key, [b"abc", b"def"], max_bytes=100)
    assert written == 6
    assert storage.exists(key)
    assert storage.size(key) == 6
    with storage.open(key) as stream:
        assert stream.read() == b"abcdef"


def test_an_oversize_stream_leaves_nothing_behind(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    with pytest.raises(ObjectTooLarge):
        storage.write(key, [b"x" * 10, b"y" * 10], max_bytes=15)
    assert not storage.exists(key)
    # Not even the partial file the write was streaming into.
    assert list((tmp_path / "uploads").iterdir()) == []


def test_a_failing_stream_leaves_nothing_behind(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")

    def exploding() -> "list[bytes]":
        raise OSError("the client went away")

    with pytest.raises(OSError, match="went away"):
        storage.write(key, exploding(), max_bytes=100)
    assert list((tmp_path / "uploads").iterdir()) == []


def test_reading_or_sizing_a_missing_object_raises(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    with pytest.raises(ObjectNotFound):
        storage.open(key)
    with pytest.raises(ObjectNotFound):
        storage.size(key)
    storage.delete(key)  # deleting what is not there is not an error


def test_an_existing_object_is_never_clobbered(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    storage.write(key, [b"first"], max_bytes=100)
    with pytest.raises(FileExistsError):
        storage.write(key, [b"second"], max_bytes=100)
    with storage.open(key) as stream:
        assert stream.read() == b"first"


def test_a_symlink_at_the_destination_is_not_followed(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    target = tmp_path / "outside.txt"
    target.write_text("do not overwrite me")
    os.symlink(target, storage.path_for(key).with_name(f"{key}.part"))
    with pytest.raises(OSError):
        storage.write(key, [b"attacker"], max_bytes=100)
    assert target.read_text() == "do not overwrite me"


def test_stored_files_are_not_readable_by_other_users(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    storage.write(key, [b"private footage"], max_bytes=100)
    assert storage.path_for(key).stat().st_mode & 0o077 == 0


def test_discard_on_error_removes_the_object(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    key = new_storage_key(".mp4")
    storage.write(key, [b"abc"], max_bytes=100)
    with pytest.raises(ValueError, match="boom"), discard_on_error(storage, key):
        raise ValueError("boom")
    assert not storage.exists(key)
