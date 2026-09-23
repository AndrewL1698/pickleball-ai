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
    PYTHONUNBUFFERED=1 \
    # `uv run` re-syncs the workspace before running, which at container start
    # would re-download the whole ML dependency tree on every boot and race the
    # healthcheck. The image is already synced, so nothing may sync again.
    UV_NO_SYNC=1

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

# Only ml/pyproject.toml is needed, and it is already copied above: uv wants
# the manifest to resolve the workspace, but pickleball-ml is not in the
# worker's dependency closure, so its sources never reach the image.
COPY apps/api/ apps/api/
COPY workers/video_processor/ workers/video_processor/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package pickleball-worker

ENV PATH="/app/.venv/bin:$PATH"

# Not root. The uploads volume is mounted at /data/uploads, so it has to be
# writable by this user; the api and worker services both mount it.
RUN useradd --create-home --uid 10001 pickleball \
    && mkdir -p /data/uploads \
    && chown -R pickleball:pickleball /app /data
USER pickleball

EXPOSE 8000
# The console script directly, not through `uv run`: the environment is already
# built, and this keeps startup to the time it takes Python to import.
CMD ["pbapi", "--host", "0.0.0.0"]
