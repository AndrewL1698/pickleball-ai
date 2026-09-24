"""Helpers shared by the API tests.

Named so pytest does not collect it as a test module, matching
`ml/tests/tests_support_camera.py`. The pieces the worker's suite also needs
live in `pickleball_api.testing`, because the two suites are separate pytest
rootdirs and can only share code through the package.
"""

import httpx
from fastapi.testclient import TestClient

from pickleball_api.testing import ALEMBIC_INI, FTYP_HEADER, video_bytes

__all__ = ["ALEMBIC_INI", "FTYP_HEADER", "upload", "video_bytes"]


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
