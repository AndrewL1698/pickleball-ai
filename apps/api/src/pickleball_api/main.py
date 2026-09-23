"""The FastAPI application.

Nothing here creates database tables: schema changes go through Alembic, so a
missing migration fails loudly instead of being papered over at startup.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pickleball_api.config import Settings, get_settings
from pickleball_api.queue import RedisJobQueue
from pickleball_api.routers import health, jobs, videos
from pickleball_api.schemas import ErrorResponse
from pickleball_api.storage import LocalFileStorage

logger = logging.getLogger(__name__)

TITLE = "Pickleball Match Intelligence API"

#: Bumped when a response shape changes incompatibly.
API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Taking settings as an argument keeps tests from
    having to reach into the environment to get a different configuration."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Resolved once, at startup: a storage root that is created and
        # resolved per request would follow a symlink swapped in later.
        app.state.storage = LocalFileStorage(settings.upload_dir)
        app.state.queue = RedisJobQueue.from_settings(settings)
        logger.info("api started in %s", settings.environment)
        yield

    app = FastAPI(
        title=TITLE,
        version=API_VERSION,
        lifespan=lifespan,
        debug=False,
    )

    # Without this, any page the user visits can reach a loopback API that has
    # no authentication, via a hostname that resolves to 127.0.0.1.
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    app.include_router(health.router)
    app.include_router(videos.router)
    app.include_router(jobs.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = ErrorResponse(error_code=_code_for(exc.status_code), detail=str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(error_code="invalid_request", detail="The request was not valid.")
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the log; the client gets a sentence. Exception
        # text routinely contains filesystem paths and connection strings.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        body = ErrorResponse(error_code="internal_error", detail="Something went wrong.")
        return JSONResponse(status_code=500, content=body.model_dump())

    return app


_STATUS_CODES = {
    404: "not_found",
    413: "upload_too_large",
    415: "unsupported_file_type",
    503: "dependency_unavailable",
    507: "insufficient_storage",
}


def _code_for(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"http_{status_code}")


app = create_app()
