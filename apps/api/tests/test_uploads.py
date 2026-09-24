"""Filename hygiene and format sniffing, away from HTTP."""

import io

import pytest
from tests_support_api import FTYP_HEADER, video_bytes

from pickleball_api.config import VIDEO_CONTENT_TYPES
from pickleball_api.uploads import (
    MAX_FILENAME_CHARS,
    UploadRejected,
    clean_filename,
    content_type_for,
    looks_like_video,
    read_chunks,
    video_extension,
)

ALLOWED = tuple(VIDEO_CONTENT_TYPES)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("match.mp4", "match.mp4"),
        ("../../etc/passwd.mp4", "passwd.mp4"),
        ("..\\..\\windows\\evil.mp4", "evil.mp4"),
        ("/tmp/match.mp4", "match.mp4"),
        ("  match.mp4  ", "match.mp4"),
        ("...match.mp4", "match.mp4"),
        ("ma\x07tch\x00.mp4", "match.mp4"),
        ("éclair.mp4", "éclair.mp4"),  # macOS sends the decomposed form
    ],
)
def test_filenames_are_reduced_to_a_bare_display_name(given: str, expected: str) -> None:
    assert clean_filename(given) == expected


def test_a_very_long_filename_is_truncated() -> None:
    assert len(clean_filename("a" * 5000 + ".mp4")) == MAX_FILENAME_CHARS


@pytest.mark.parametrize("given", [None, "", "   ", "...", "/", "\x00"])
def test_a_filename_with_nothing_in_it_is_rejected(given: str | None) -> None:
    with pytest.raises(UploadRejected):
        clean_filename(given)


@pytest.mark.parametrize("name", ["m.mp4", "m.MP4", "m.MoV", "m.m4v"])
def test_allowed_extensions_are_matched_case_insensitively(name: str) -> None:
    assert video_extension(name, ALLOWED) in ALLOWED


@pytest.mark.parametrize("name", ["m.txt", "m.mkv", "m", "m.mp4.exe", "m.mp"])
def test_other_extensions_are_rejected(name: str) -> None:
    with pytest.raises(UploadRejected, match="accepted"):
        video_extension(name, ALLOWED)


def test_every_allowed_extension_has_a_content_type() -> None:
    assert {content_type_for(e) for e in ALLOWED} == set(VIDEO_CONTENT_TYPES.values())


def test_the_iso_base_media_header_is_what_identifies_a_video() -> None:
    assert looks_like_video(FTYP_HEADER)
    assert not looks_like_video(b"<html><body>not a video</body></html>")
    assert not looks_like_video(b"")
    assert not looks_like_video(b"ftyp" + b"\x00" * 8)  # right marker, wrong offset


def test_chunks_come_back_whole_and_in_order() -> None:
    content = video_bytes(5000)
    chunks = list(read_chunks(io.BytesIO(content), chunk_bytes=1000))
    assert b"".join(chunks) == content
    assert len(chunks) == 5


def test_a_file_that_is_not_a_video_is_refused_before_anything_is_written() -> None:
    chunks = read_chunks(io.BytesIO(b"PK\x03\x04 a zip file, renamed"))
    with pytest.raises(UploadRejected, match="mp4/mov"):
        next(chunks)


def test_a_file_too_short_to_identify_is_refused() -> None:
    with pytest.raises(UploadRejected):
        next(read_chunks(io.BytesIO(b"tiny")))
