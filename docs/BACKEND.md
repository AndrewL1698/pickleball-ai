# Backend

The FastAPI service, the database, the storage layer and the job worker added in
Phase 1. This document covers what exists after checkpoint 1; the architecture
this is heading towards is in `ARCHITECTURE.md`, and the eventual schema is in
`DATA_MODEL.md`.

## What This Checkpoint Does and Does Not Do

It does: accept a video upload, record it, store the file, queue a job, let a
worker claim that job and drive it through `queued -> running -> ready` (or
`failed`), and expose enough over HTTP for a frontend to list and inspect both.

It does not analyze anything. The worker runs a **placeholder processor** that
reads the stored file and records its SHA-256. That is deliberate: the job
plumbing -- claiming, progress, terminal states, crash recovery -- is worth
getting right on its own, and it can be tested in milliseconds rather than
minutes. The real processor replaces one object behind one interface
(`pickleball_worker.processors.VideoProcessor`).

There is also no frontend, no authentication, and no `Match` entity yet.

## Directory Structure

```text
apps/api/                              # package `pickleball_api`
├── alembic.ini                        # migrations config; the DB URL comes from settings
├── Dockerfile                         # shared by the api and worker compose services
├── migrations/
│   ├── env.py                         # reads the URL from settings unless given one
│   └── versions/0001_video_and_analysis_job.py
├── src/pickleball_api/
│   ├── config.py                      # typed settings, and the extension allowlist
│   ├── db.py                          # engine, session factory, request/worker sessions
│   ├── dependencies.py                # what routes ask for: settings, session, storage, queue
│   ├── errors.py                      # job error codes and their user-safe messages
│   ├── jobs.py                        # the job state machine and the read queries
│   ├── main.py                        # create_app(): middleware, routers, error handlers
│   ├── models.py                      # SQLAlchemy tables: Video, AnalysisJob
│   ├── queue.py                       # JobQueue interface, RQ and recording implementations
│   ├── schemas.py                     # Pydantic response models
│   ├── storage.py                     # Storage interface, local filesystem implementation
│   ├── uploads.py                     # filename hygiene, allowlist, format sniffing
│   ├── routers/{health,videos,jobs}.py
│   └── __main__.py                    # `pbapi`
└── tests/

workers/video_processor/               # package `pickleball_worker`
├── src/pickleball_worker/
│   ├── processors.py                  # VideoProcessor interface + PlaceholderProcessor
│   ├── tasks.py                       # the RQ task and its transaction boundaries
│   └── __main__.py                    # `pbworker`
└── tests/
```

Both are uv workspace members alongside `ml/`.

## Dependency Direction

```text
pickleball_ml      imports nothing from this repository
pickleball_api     imports neither pickleball_ml nor pickleball_worker
pickleball_worker  imports pickleball_api, and will import pickleball_ml
```

This is what keeps torch and ultralytics out of the API process. The API
enqueues by dotted path (`pickleball_worker.tasks.run_analysis_job`) rather than
by importing the function, so the edge really does point one way.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/videos` | Multipart upload. Creates a `Video` and a queued `AnalysisJob`. `201` |
| `GET` | `/api/videos` | Videos newest first, each with its latest job. `limit`, `offset` |
| `GET` | `/api/videos/{video_id}` | One video and every job for it. `404` if unknown |
| `GET` | `/api/jobs/{job_id}` | Status, stage, progress, timestamps, failure code. `404` if unknown |
| `GET` | `/health` | Liveness. Touches no dependency |
| `GET` | `/ready` | Readiness. Checks the database and Redis; `503` if either is down |

Errors all have the same shape: `{"error_code": "...", "detail": "..."}`.

Upload rejections: `415` for an extension outside the allowlist or bytes that
are not an ISO base-media file, `413` past the size limit, `507` when the disk
has no room, `503` when the job cannot be queued.

## Data Model

Two tables. `videos`:

| Column | Notes |
|---|---|
| `id` | UUID. Public, so it is random rather than sequential |
| `original_filename` | Display only; never used to build a path |
| `storage_key` | Generated, unique, never returned by the API |
| `content_type` | Derived from the extension allowlist, not from the client |
| `byte_size` | `BIGINT`; a match exceeds `INT4` |
| `created_at` | `timestamptz`, set in Python |

`analysis_jobs`: `id`, `video_id` (FK, `ON DELETE CASCADE`), `status`, `stage`,
`progress` (0.0-1.0, with a CHECK), `error_code`, `error_message`, `created_at`,
`started_at`, `finished_at`.

`status` and `stage` are stored as `VARCHAR` plus a CHECK constraint rather than
native PostgreSQL enums, so that adding a member does not need a type migration
and so the same migration runs on the SQLite database the tests use.

### Relationship to `DATA_MODEL.md`

`DATA_MODEL.md` describes `Match` owning a `VideoAsset` and a `ProcessingJob`.
`Video` here is that `VideoAsset` minus the decoded metadata (which needs the
decoder the placeholder does not run), and `AnalysisJob` is that
`ProcessingJob`. `Match` is deliberately absent: it exists to own calibrations,
players and rallies, none of which exist yet, and an entity with one field and
no children is harder to review than the migration that adds it later.

### Job Status

```text
queued ──> running ──> ready        (terminal)
   │          │
   └──────────┴──────> failed ──> queued   (an explicit retry)
```

`ready` has no outgoing transitions at all. This is what stops an RQ retry --
which happens whenever a worker is killed mid-job -- from walking a finished job
backwards into `running` and overwriting its result. `pickleball_api.jobs.transition`
is the only way to change a status, and it raises `InvalidJobTransition` rather
than writing a contradictory row; the worker treats that refusal as "another
attempt already finished this" and leaves the first verdict alone.

The stage names follow the ladder in `ARCHITECTURE.md`. The placeholder reaches
`metadata_ready` and stops.

## The Upload Path

The order is the point:

1. Validate the filename, extension, and the first bytes.
2. Write the file under a **generated** key (`<32 hex chars>.<ext>`), streaming
   with a running byte count.
3. Insert both rows in one short transaction and commit. If that fails, the
   stored file is deleted.
4. Only then enqueue.

Step 4 after step 3 matters: RQ pushes to Redis immediately, and a worker
blocked on the queue wakes in microseconds. Enqueueing before the commit lets
the worker look for a job that is not visible yet and fail with "no such job".
The route asserts it is no longer in a transaction before it enqueues.

The file is written before the rows, not after, because the two residues are not
equally bad: an orphaned file is invisible and reclaimable, whereas a row
pointing at a file that does not exist is something every later endpoint has to
defend against.

If the queue is unreachable the rows are kept -- the upload really did happen --
and the job is marked `failed` with `enqueue_failed` rather than left claiming a
worker has it. The response is `503`.

## Storage

`Storage` is a `Protocol` with `write`/`open`/`delete`/`exists`/`size`, shaped
like an object store so an S3 implementation slots in without touching callers.
`LocalFileStorage` is the development implementation.

Nothing from the client reaches the filesystem. The key is generated, matched
against `^[0-9a-f]{32}\.[a-z0-9]{1,8}$`, and the resolved path is confirmed to
be inside the storage root before any write -- `root / key` alone is not enough,
because an absolute key replaces the root and `..` is only collapsed by
resolution. Writes go to a `.part` file opened `O_EXCL|O_NOFOLLOW` (so a planted
symlink is not followed and an existing object is not clobbered) and are renamed
into place only once the whole stream has arrived. The directory is `0700` and
files are `0600`.

## The Worker

`pbworker` runs an RQ worker against the configured queue. The task takes a job
id and nothing else: everything else is read from PostgreSQL, so the queue is
not a channel through which paths or configuration can be injected.

Three separate transactions -- claim, each progress report, and the terminal
transition -- so that a status the API can see is written as soon as it is true,
rather than all at once when the job ends. The claim locks the row and refuses
anything that is not `queued`.

On macOS the worker uses RQ's `SimpleWorker`, which runs jobs in-process. The
default forking worker aborts once a CoreFoundation-backed library is loaded,
which OpenCV and torch both are, so this would break the moment real processing
lands. Pass `--fork` to use the forking worker (the default on Linux). The
trade-off: `SimpleWorker` cannot kill a job that overruns its timeout, and a
crash takes the worker down with it.

## Configuration

Every variable is prefixed `PICKLEBALL_` and read by both processes. Copy
`.env.example` to `.env`; `.env` is gitignored.

| Variable | Default | Purpose |
|---|---|---|
| `PICKLEBALL_ENVIRONMENT` | `development` | `development`, `test`, or `production` |
| `PICKLEBALL_DATABASE_URL` | `postgresql+psycopg://pickleball:pickleball@localhost:5433/pickleball` | Database |
| `PICKLEBALL_DATABASE_ECHO` | `false` | Log every statement, with parameter values |
| `PICKLEBALL_REDIS_URL` | `redis://localhost:6379/0` | Queue backend |
| `PICKLEBALL_QUEUE_NAME` | `analysis` | RQ queue |
| `PICKLEBALL_JOB_TIMEOUT_SECONDS` | `3600` | Per-job timeout |
| `PICKLEBALL_UPLOAD_DIR` | `data/uploads` | Where uploads are written |
| `PICKLEBALL_MAX_UPLOAD_BYTES` | `2147483648` | 2 GiB |
| `PICKLEBALL_ALLOWED_VIDEO_EXTENSIONS` | `[".mp4",".mov",".m4v"]` | Each needs a `VIDEO_CONTENT_TYPES` entry |
| `PICKLEBALL_CORS_ORIGINS` | `["http://localhost:3000", ...]` | The Next.js dev server |
| `PICKLEBALL_TRUSTED_HOSTS` | `["localhost","127.0.0.1","testserver"]` | Host header allowlist |

The two URLs are `SecretStr`, so a settings repr in a log or an exception prints
`**********` rather than the password.

## Migrations

Alembic, always. Nothing creates tables at startup, so a missing migration fails
loudly rather than being papered over.

```bash
uv run alembic -c apps/api/alembic.ini upgrade head
uv run alembic -c apps/api/alembic.ini downgrade -1
uv run alembic -c apps/api/alembic.ini current
uv run alembic -c apps/api/alembic.ini revision --autogenerate -m "what changed"
```

`env.py` takes the URL from the application settings unless one is already
configured, so the migrations always target the same database the API does, and
no credential sits in a tracked file. Always read an autogenerated migration
before committing it.

## Dependencies and Processes

```bash
docker compose up -d postgres redis     # the usual case
docker compose --profile app up -d      # ...plus containerized api and worker
docker compose down                     # stop; add -v to discard the data
```

Both are published on `127.0.0.1` only. PostgreSQL is on **5433**, not 5432, so
it does not collide with a PostgreSQL already installed on the machine.

The `app` profile is off by default: the intended local setup is dependencies in
Docker and Python natively, because the worker will need the GPU and Docker on
macOS cannot reach it.

## Testing

```bash
uv run pytest                    # no database, Redis, or video required
uv run pytest -m integration     # needs docker compose up -d postgres redis
```

The default suite runs against in-memory SQLite with the **real Alembic
migration** applied, rather than `create_all`, so a migration that does not
produce the schema the models expect fails there. Foreign keys are switched on
explicitly, since SQLite leaves them off and the tests would otherwise be more
permissive than production. The queue is a recording double; `test_queue.py`
covers the real RQ adapter against `fakeredis`.

The integration tests apply the migrations to a throwaway PostgreSQL database,
compare the result against the models (catching a model edited without a new
migration), and roll it back again.

## Security Notes

No authentication yet, so the API binds to `127.0.0.1` by default and
`TrustedHostMiddleware` rejects unexpected `Host` headers -- without it, any page
the user visits could reach a loopback API through a hostname that resolves to
127.0.0.1. CORS lists the dev origin explicitly, with credentials off.

Failures never surface internals. A job stores a code from
`pickleball_api.errors.JobErrorCode` and the fixed sentence that goes with it,
never `str(exception)`, which for a database error contains the full connection
string. `/ready` returns booleans and never says why. Unhandled errors are
logged with their traceback and answered with one sentence.

## What Is Left

Checkpoint 3 of Phase 1 (checkpoint 2 added the web app; see `FRONTEND.md`):

- a `Match` entity, so a video belongs to something calibrations can hang off
- real video metadata extraction (fps, dimensions, duration, frame count,
  rotation) via `pickleball_ml.video.reader.read_metadata`, replacing the
  placeholder processor
- serving uploaded video back to the browser, which needs its own decisions
  about origin, `Content-Disposition`, and `X-Content-Type-Options`

Known gaps in what is here:

- **Nothing re-queues a job whose enqueue failed.** It is marked `failed` and
  visible, but there is no retry endpoint and no sweeper.
- **A job whose worker is killed stays `running` forever.** There is no reaper.
- **No authentication or per-user authorization.** Every video is visible to
  anyone who can reach the port.
- **No deletion endpoint**, which `ARCHITECTURE.md` requires for privacy.
- Starlette spools the whole upload to a temporary file before the route sees
  it, so the size limit bounds what is stored, not what is buffered. A
  production deployment needs a body limit at the reverse proxy too.
