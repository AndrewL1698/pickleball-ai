"""Helpers shared by the API tests.

Named so pytest does not collect it as a test module, matching
`ml/tests/tests_support_camera.py`.
"""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"

#: A real ISO base-media header, so an upload gets past the format sniff.
FTYP_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def video_bytes(size: int = 4096) -> bytes:
    """A fake video: a genuine `ftyp` box followed by filler."""
    return FTYP_HEADER + b"\x00" * max(size - len(FTYP_HEADER), 0)


def upload(
    client: TestClient,
    filename: str,
    *,
    size: int = 4096,
    content_type: str = "video/mp4",
) -> httpx.Response:
    """POST one video to the upload endpoint."""
    return client.post(
        "/api/videos", files={"file": (filename, video_bytes(size), content_type)}
    )
