"""Deciding whether an uploaded file is a video we will accept, and storing it.

Three things about the request are attacker-controlled and treated as such:

- The filename. It is kept only to show the user what they uploaded; no part of
  it reaches the filesystem. The extension is looked up in an allowlist, and the
  content type stored is the one that allowlist gives, not the one the client
  declared (phones routinely send `application/octet-stream` for a `.mov`).
- The declared size. `Content-Length` can lie and is absent for chunked bodies,
  so the limit is enforced by counting the bytes as they arrive.
- The contents. The first bytes are checked for an ISO base-media `ftyp` box.
  That is a sanity check against a text file with a `.mp4` name, not a security
  boundary; the real verdict comes from the decoder in the worker.
"""

import unicodedata
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import BinaryIO

from pickleball_api.config import VIDEO_CONTENT_TYPES
from pickleball_api.storage import CHUNK_BYTES

#: Longest original filename kept, in characters. The column is 255.
MAX_FILENAME_CHARS = 200

#: Longest default match name, in characters. The column is 200.
MAX_MATCH_NAME_CHARS = 200
UNNAMED_MATCH = "Untitled match"

#: ISO base media files (mp4/m4v/mov) carry an `ftyp` box at offset 4.
FTYP_OFFSET = 4
FTYP_MARKER = b"ftyp"
SNIFF_BYTES = 12


class UploadRejected(Exception):
    """The upload is not something this API accepts."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def clean_filename(filename: str | None) -> str:
    """The uploaded name, made safe to store and display.

    This value is metadata only, so the work here is hygiene rather than
    defence: drop any directory part a browser included, normalize so macOS's
    decomposed form compares equal to everyone else's, remove control
    characters that would corrupt a log line, and bound the length.
    """
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if c >= " " and c != "\x7f")
    name = name.strip().lstrip(".")
    if not name:
        raise UploadRejected("missing_filename", "The upload has no usable filename.")
    if len(name) <= MAX_FILENAME_CHARS:
        return name
    # Truncate the stem, not the whole name: cutting the extension off would
    # turn a legitimate long filename into an unsupported file type.
    suffix = PurePosixPath(name).suffix[: MAX_FILENAME_CHARS // 2]
    return name[: MAX_FILENAME_CHARS - len(suffix)] + suffix


def default_match_name(filename: str) -> str:
    """A match name to start with, taken from an already-cleaned filename.

    The extension is dropped and runs of whitespace collapsed, so
    `"Sunday  doubles.mov"` becomes `"Sunday doubles"`. It is display text
    only, like the filename it came from. Migration 0003 carries a frozen copy
    of this rule for the matches it backfilled.
    """
    name = " ".join(PurePosixPath(filename).stem.split())[:MAX_MATCH_NAME_CHARS].strip()
    return name or UNNAMED_MATCH


def video_extension(filename: str, allowed: tuple[str, ...]) -> str:
    """The allowlisted extension of a filename, or a rejection."""
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in allowed:
        raise UploadRejected(
            "unsupported_file_type",
            f"Only {', '.join(sorted(allowed))} files are accepted.",
        )
    return extension


def content_type_for(extension: str) -> str:
    """The content type stored for an extension. Never the client's claim."""
    return VIDEO_CONTENT_TYPES[extension]


def looks_like_video(head: bytes) -> bool:
    """Whether the first bytes are an ISO base-media header."""
    return head[FTYP_OFFSET:FTYP_OFFSET + len(FTYP_MARKER)] == FTYP_MARKER


def read_chunks(stream: BinaryIO, chunk_bytes: int = CHUNK_BYTES) -> Iterator[bytes]:
    """The stream as chunks, with the first one checked for a video header.

    The check happens inside the generator so the caller streams once: the head
    is inspected and then handed on rather than being read and discarded.
    """
    head = stream.read(chunk_bytes)
    if len(head) < SNIFF_BYTES or not looks_like_video(head):
        raise UploadRejected(
            "unsupported_file_type", "The file is not a readable mp4/mov video."
        )
    yield head
    while chunk := stream.read(chunk_bytes):
        yield chunk
