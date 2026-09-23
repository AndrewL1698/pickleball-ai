# Image for both the API and the worker: they share a package and differ only
# in the command docker-compose gives them.
#
# This is for running the stack without a local Python toolchain. The normal
# development loop runs both natively (see README), because the worker will
# eventually need the GPU and Docker on macOS cannot reach it.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The workspace members' manifests are needed before the sources, so that a
# code change does not invalidate the cached dependency install.
COPY pyproject.toml uv.lock ./
COPY ml/pyproject.toml ml/
COPY apps/api/pyproject.toml apps/api/
COPY workers/video_processor/pyproject.toml workers/video_processor/

# --no-install-workspace: dependencies only, in their own cached layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package pickleball-worker

COPY ml/ ml/
COPY apps/api/ apps/api/
COPY workers/video_processor/ workers/video_processor/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package pickleball-worker

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uv", "run", "pbapi", "--host", "0.0.0.0"]
