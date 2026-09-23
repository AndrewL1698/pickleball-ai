"""Refusing an oversized upload before its bytes are on disk.

FastAPI resolves an `UploadFile` parameter by parsing the whole multipart body
first, and Starlette spools each file part to a temporary file with no total
cap. By the time the route can count bytes, the server has already written all
of them to `$TMPDIR`. The limit is still enforced -- nothing oversized is ever
stored -- but the disk cost has already been paid, which on an endpoint with no
authentication is a way to fill a filesystem.

This middleware closes the common case by refusing on `Content-Length` before
the body is read at all. It is not a complete answer: a chunked request sends
no length, so the counting check in `storage.write` remains the backstop. A
deployment behind a reverse proxy should also set a body limit there.
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.status import HTTP_413_CONTENT_TOO_LARGE

from pickleball_api.schemas import ErrorResponse

logger = logging.getLogger(__name__)


def content_length_of(request: Request) -> int | None:
    """The declared body size, or None when absent or unparseable."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def limit_upload_size(
    max_bytes: int,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Middleware that rejects a declared body larger than `max_bytes`.

    The header is client-supplied and so is not trusted as the only check; it
    is trusted only to say "this is definitely too big", which is the one
    direction a lying client cannot exploit.

    It applies to every request rather than to a list of upload paths. No
    endpoint here legitimately accepts a body larger than the upload limit, and
    a path list would fail open: adding a second upload route, or moving this
    one under a version prefix, would silently drop the guard with no test
    noticing.
    """

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = content_length_of(request)
        if declared is not None and declared > max_bytes:
            logger.info("refused a %d byte body before reading it", declared)
            # Built here rather than raised: an HTTPException from an HTTP
            # middleware does not reach the app's exception handlers, so the
            # response has to be assembled with the same model they use.
            return JSONResponse(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                content=ErrorResponse(
                    error_code="upload_too_large",
                    detail=too_large_message(max_bytes),
                ).model_dump(),
            )
        return await call_next(request)

    return middleware


def too_large_message(max_bytes: int) -> str:
    """The one sentence used wherever a body exceeds the limit."""
    return f"The video is larger than the {max_bytes} byte limit."
