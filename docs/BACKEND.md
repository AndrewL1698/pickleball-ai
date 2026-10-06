# Backend

The FastAPI service, the database, the storage layer and the job worker added in
Phase 1, re-parented under `Match` in Phase 2 checkpoint 1, and given real video
metadata extraction in checkpoint 2. The architecture
this is heading towards is in `ARCHITECTURE.md`, and the eventual schema is in
`DATA_MODEL.md`.

## What This Does and Does Not Do

It does: accept a video upload as a new match, store the file, record the match,
its video and a job, queue the job, let a worker claim it, decode the video's
metadata and drive the job through `queued -> running -> ready` (or `failed`)
while the match's status follows, retry extraction on request, and expose
enough over HTTP for a frontend to list and inspect matches.

It does not analyse the match. The worker's `MetadataProcessor` reads the
video's dimensions, rotation, average frame rate, duration, frame count and
codec with `pickleball_ml.video.reader.read_metadata`, and nothing else: no
frame is analysed and no model is loaded. It sits behind the same interface
(`pickleball_worker.processors.VideoProcessor`) that later stages will use.

There is no authentication, no video playback and no calibration yet. The web
app that drives this API is described in `FRONTEND.md`.

## Directory Structure

```text
apps/api/                              # package `pickleball_api`
├── alembic.ini                        # migrations config; the DB URL comes from settings
├── Dockerfile                         # shared by the api and worker compose services
├── migrations/
│   ├── env.py                         # reads the URL from settings unless given one
│   └── versions/                      # 0001 the tables, 0002 enum CHECKs, 0003 matches
├── src/pickleball_api/
│   ├── config.py                      # typed settings, and the extension allowlist
│   ├── db.py                          # engine, session factory, request/worker sessions
│   ├── dependencies.py                # what routes ask for: settings, session, storage, queue
│   ├── errors.py                      # job error codes and their user-safe messages
│   ├── jobs.py                        # the job state machine, match status sync, read queries
│   ├── main.py                        # create_app(): middleware, routers, error handlers
│   ├── models.py                      # SQLAlchemy tables: Match, Video, AnalysisJob
│   ├── queue.py                       # JobQueue interface, RQ and recording implementations
│   ├── schemas.py                     # Pydantic response models
│   ├── limits.py                      # refuses an oversized body before it is read
│   ├── storage.py                     # Storage interface, local filesystem implementation
│   ├── testing.py                     # fixtures both test suites share
│   ├── uploads.py                     # filename hygiene, default match name, allowlist, sniffing
│   ├── routers/{health,matches,jobs}.py
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
pickleball_worker  imports pickleball_api and pickleball_ml.video
```

This is what keeps torch and ultralytics out of the API process. The worker
imports only `pickleball_ml.video.reader`, and depends on `pickleball-ml`
**without** its `tracking` extra: the base install is OpenCV, NumPy and the
light libraries the pipeline modules import, while `ultralytics` (and through it
torch) and `lap` are the extra, which the workspace root installs for the Phase 0
CLI. So the worker image contains neither torch nor ultralytics, and a test
asserts that importing the worker loads neither. The API
enqueues by dotted path (`pickleball_worker.tasks.run_analysis_job`) rather than
by importing the function, so the edge really does point one way.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/matches` | Multipart upload (`file`). Creates a `Match`, its `Video` and a queued `AnalysisJob`. `201` |
| `GET` | `/api/matches` | Matches newest first, each with its video and latest job. `limit`, `offset` |
| `GET` | `/api/matches/{match_id}` | One match, its video and metadata, its latest job and every job attempt. `404` if unknown |
| `POST` | `/api/matches/{match_id}/metadata-jobs` | Queue a metadata extraction job. `202` with the match; `409` if refused; `503` if it cannot be queued |
| `GET` | `/api/jobs/{job_id}` | Status, stage, progress, timestamps, failure code, `match_id`. `404` if unknown |
| `GET` | `/health` | Liveness. Touches no dependency |
| `GET` | `/ready` | Readiness. Checks the database and Redis; `503` if either is down |

Errors all have the same shape: `{"error_code": "...", "detail": "..."}`.

The Phase 1 `/api/videos` endpoints were **removed**, not aliased: a video is
now something a match owns, and two collections describing the same uploads
would drift apart. They answer `404`, which a test pins. The API version in the
OpenAPI document moved to `0.2.0` for the break.

### Response shapes

`MatchDetail` (from `POST /api/matches` and `GET /api/matches/{id}`):

```json
{
  "id": "…uuid…",
  "name": "Sunday doubles",
  "recorded_at": null,
  "status": "calibration_required",
  "created_at": "2026-09-24T22:10:12.9Z",
  "video": {
    "id": "…uuid…",
    "original_filename": "Sunday  doubles.mp4",
    "content_type": "video/mp4",
    "byte_size": 4032,
    "created_at": "2026-09-24T22:10:12.9Z"
  },
  "latest_job": { "id": "…", "match_id": "…", "status": "ready", "stage": "metadata_ready", "…": "…" },
  "jobs": [ "…every attempt, oldest first…" ]
}
```

`video.metadata` is null until the worker has decoded the video, and otherwise:

```json
{
  "width": 1080, "height": 1920, "rotation_degrees": 90,
  "average_fps": 29.97, "duration_seconds": 754.2, "frame_count": 22603,
  "codec": "hvc1", "extracted_at": "2026-09-25T18:40:01.12Z"
}
```

`width` and `height` are in display orientation, after `rotation_degrees` was
applied. `average_fps` is named for what it is: phone video is often variable
frame rate, so it is not exact frame timing, and `duration_seconds` (frame count
over the average) is an estimate. `codec` is the stream's FourCC, or null.
`can_extract_metadata` says whether `POST .../metadata-jobs` would be accepted
right now, so the UI never offers an action the API will refuse.

`MatchSummary` (each item of `GET /api/matches`) is the same without `jobs` and
`can_extract_metadata`.
`video` is nullable in the schema because the database permits a match without
one, but the upload path never produces it. The calibration will be added to
`MatchDetail` when it exists; there is no placeholder field for it now.
`storage_key` appears in no response, and a test checks the OpenAPI document
for it.

Upload rejections: `415` for an extension outside the allowlist or bytes that
are not an ISO base-media file, `413` past the size limit, `507` when the disk
has no room, `503` when the job cannot be queued.

## Data Model

Three tables. `matches` is the parent:

| Column | Notes |
|---|---|
| `id` | UUID. Public, so it is random rather than sequential |
| `name` | Defaults to the upload's filename without its extension, whitespace collapsed; `Untitled match` if nothing is left |
| `recorded_at` | When the game was played. Nullable, and nothing sets it yet |
| `status` | `uploaded`, `processing`, `calibration_required`, `court_ready`, `failed` |
| `created_at` | `timestamptz`, set in Python |

`videos`, one per match:

| Column | Notes |
|---|---|
| `id` | UUID. Returned by the API, but no endpoint takes it |
| `match_id` | FK to `matches`, `NOT NULL`, `UNIQUE`, `ON DELETE CASCADE` |
| `original_filename` | Display only; never used to build a path |
| `storage_key` | Generated, unique, never returned by the API |
| `content_type` | Derived from the extension allowlist, not from the client |
| `byte_size` | `BIGINT`; a match exceeds `INT4` |
| `created_at` | `timestamptz`, set in Python |
| `width`, `height` | Display orientation. Nullable; `> 0` |
| `rotation_degrees` | `0`, `90`, `180` or `270`. Nullable |
| `average_fps` | Nullable; `> 0`. An average, not exact timing |
| `duration_seconds`, `frame_count` | Nullable; `>= 0`. Duration is frame count over average rate |
| `codec` | FourCC, `VARCHAR(32)`. Nullable even once decoded |
| `metadata_extracted_at` | Null until decoded. A CHECK requires every decoded field to be present with it, and absent without it |

`analysis_jobs`: `id`, `match_id` (FK, `ON DELETE CASCADE`), `status`, `stage`,
`progress` (0.0-1.0, with a CHECK), `error_code`, `error_message`, `created_at`,
`started_at`, `finished_at`. Indexed on `(match_id, created_at)`, which serves
both "every job for this match" and "its latest job". The latest job is the
newest `created_at`, ties broken by `id`. A partial unique index,
`ux_analysis_jobs_one_active_per_match`, allows at most one `queued` or
`running` job per match; job history is unaffected.

`status` and `stage` are stored as `VARCHAR` plus a CHECK constraint (added in
migration `0002`) rather than native PostgreSQL enums, so that adding a member
does not need a type migration and so the same migration runs on the SQLite
database the tests use.

`matches.status` follows the same pattern, with its CHECK created by `0003`.

### Relationship to `DATA_MODEL.md`

`DATA_MODEL.md` describes `Match` owning a `VideoAsset` and a `ProcessingJob`,
and that is now the shape here. `Video` is that `VideoAsset`, with its decoded
metadata columns named for what they hold (`average_fps`), and `AnalysisJob` is
that `ProcessingJob`. `Match` has no `user_id` because there
are no users.

### Match Status

A job's transition drives its match, in the same transaction, and nowhere else
does (`pickleball_api.jobs.transition`):

| Job moves to | Match becomes |
|---|---|
| `queued` (a requeue after failure) | `uploaded` |
| `running` | `processing` |
| `ready` | `calibration_required` — unless it is already `court_ready` |
| `failed` | `failed` |

A new upload starts at `uploaded`. `court_ready` is reserved for the
calibration checkpoint; nothing sets it yet. There is no `ready`, on purpose.

`calibration_required` now also means "metadata extracted": the worker saves
the metadata in the same commit as the `ready` transition that sets it.

### Retrying Extraction

`POST /api/matches/{match_id}/metadata-jobs` starts a new metadata job. It is
accepted when the match has a video, its metadata has never been extracted, and
no job is queued or running -- which covers both a failed attempt and every
match that predates extraction. It is refused with `409` otherwise: the three
reasons are `Match.metadata_job_refusal()`, and each has a fixed sentence.

- Nothing is enqueued from a migration or at startup. Backfilled matches wait
  for someone to ask.
- A new job is added; earlier jobs, failed or not, stay in the history.
- The job is committed before it is enqueued, and a queue failure marks it
  `failed` with `enqueue_failed` and answers `503`, exactly as the upload does
  (both go through `_enqueue_committed`).
- Concurrency: the match row is locked (`SELECT ... FOR UPDATE`) while the rule
  is checked, so two requests take turns on PostgreSQL. The partial unique index
  is the backstop: a second active job fails at commit and is answered `409`.
  An integration test fires eight simultaneous requests at PostgreSQL and
  expects exactly one `202`.

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

The stage names follow the ladder in `ARCHITECTURE.md`. Metadata extraction
reaches `metadata_ready` and stops; the job stays at `ingested` while it runs,
and only the task layer sets `metadata_ready`, once the metadata is saved.

## The Upload Path

The order is the point:

1. Validate the filename, extension, and the first bytes.
2. Write the file under a **generated** key (`<32 hex chars>.<ext>`), streaming
   with a running byte count.
3. Insert the match, its video and its job in one short transaction and
   commit. If that fails, the stored file is deleted.
4. Only then enqueue.

Step 4 after step 3 matters: RQ pushes to Redis immediately, and a worker
blocked on the queue wakes in microseconds. Enqueueing before the commit lets
the worker look for a job that is not visible yet and fail with "no such job".
The route asserts it is no longer in a transaction before it enqueues.

The file is written before the rows, not after, because the two residues are not
equally bad: an orphaned file is invisible and reclaimable, whereas a row
pointing at a file that does not exist is something every later endpoint has to
defend against.

If the queue is unreachable the rows and the file are kept -- the upload really
did happen -- and the job is marked `failed` with `enqueue_failed`, which fails
the match with it, rather than being left claiming a worker has it. The
response is `503` with a fixed sentence; no Redis URL or path reaches it.

## Storage

`Storage` is a `Protocol` with `write`/`open`/`local_path`/`delete`/`exists`/
`size`, shaped like an object store so an S3 implementation slots in without
touching callers. `LocalFileStorage` is the development implementation.

`local_path(key)` is a context manager yielding a local file for code that can
only read a path -- the video decoder. `LocalFileStorage` yields its own
validated file, with no copy; an object-store implementation would download to
a temporary file and delete it on exit, which is why callers use the path only
inside the `with` block and treat it as read-only. It raises `ObjectNotFound` on
entry. Nothing reads a whole video into memory.

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
id and nothing else: everything else is read from PostgreSQL (job -> match ->
video), so the queue is not a channel through which paths or configuration can
be injected. A job whose match has no video is failed with
`missing_video_file` during the claim, without ever reaching `running`.

### Metadata Extraction

`MetadataProcessor.process` checks the stored object's size against the upload
record, opens it with `storage.local_path`, calls `read_metadata`, and returns a
typed `ProcessingResult`. It never touches the database: the task layer's
`_complete` locks the job, writes the metadata onto the video and moves the job
to `ready` at `metadata_ready` -- and the match to `calibration_required` -- in
one commit. If that commit fails (a CHECK refuses the values, the database goes
away) the job is failed with `internal` instead; it is never `ready` without
its metadata. A job that is no longer `running` when the result arrives keeps
the verdict and metadata it already has.

| Failure | Code | Metadata |
|---|---|---|
| Object missing from storage | `missing_video_file` | left null |
| Stored size differs from the upload record | `unreadable_video` | left null |
| OpenCV cannot open it (`VideoReadError`) | `unreadable_video` | left null |
| Implausible values: zero size, no frame rate, non-right-angle rotation (`InvalidVideoMetadata`) | `unreadable_video` | left null |
| Anything else (a bug) | `internal`, traceback logged | left null |

The user sees only the code's fixed sentence. The decoder's message, the
storage key and the filesystem path go to the worker log.

`read_metadata` sets OpenCV's auto-orientation explicitly, so width, height and
frames are all in display orientation, and validates everything: rotation is
normalised into `{0, 90, 180, 270}` (so `-90` becomes `270`), and a zero,
negative or non-finite value is refused rather than stored.

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
| `PICKLEBALL_TRUSTED_HOSTS` | `["localhost","127.0.0.1"]` | Host header allowlist |

The two URLs are `SecretStr`, so a settings repr in a log or an exception prints
`**********` rather than the password.

## Migrations

Alembic, always. Nothing creates tables at startup, so a missing migration fails
loudly rather than being papered over.

Run these from the repository root. `upload_dir` and `.env` are both resolved
relative to the working directory, so a command run from elsewhere quietly uses
a different database and a different upload directory. Both the API and the
worker log the upload directory they resolved, which is the quickest way to
confirm it.

```bash
uv run alembic -c apps/api/alembic.ini upgrade head
uv run alembic -c apps/api/alembic.ini downgrade -1
uv run alembic -c apps/api/alembic.ini current
uv run alembic -c apps/api/alembic.ini revision --autogenerate -m "what changed"
```

Alembic does not autogenerate CHECK constraints, so a change to `JobStatus`,
`JobStage` or `MatchStatus` needs its migration written by hand; `0002` is the
pattern.

### Migration 0003: introducing `matches`

`0003_match_ownership` preserves every Phase 1 row. Each existing video becomes
a match of its own, **reusing the video's id** (so a saved `/videos/<id>` link
can be redirected to `/matches/<id>`), named from its filename, with
`created_at` copied from the video and status derived from its latest job
(`queued -> uploaded`, `running -> processing`, `ready -> calibration_required`,
`failed -> failed`; no job -> `uploaded`). Every job moves from `video_id` to
that match's id. Then `videos.match_id` becomes `NOT NULL` and `UNIQUE`, and
`analysis_jobs.video_id` is dropped.

The downgrade restores `analysis_jobs.video_id` from each match's video and
drops `matches`. Match names and statuses have no Phase 1 column and are lost.
A job whose match has no video cannot be represented before 0003, so the
downgrade refuses with an error rather than deleting it.

On SQLite, `batch_alter_table` rebuilds a table by dropping the original, and
with foreign keys on that drop fires `ON DELETE CASCADE` on anything still
referencing it. The migration orders its steps so a table is only rebuilt when
nothing points at it; `test_migration_0003.py` would catch a regression by
counting rows on both sides.

`env.py` takes the URL from the application settings unless one is already
configured, so the migrations always target the same database the API does, and
no credential sits in a tracked file. Always read an autogenerated migration
before committing it.

### Migration 0004: video metadata

`0004_video_metadata` adds the nullable metadata columns and their CHECK
constraints, and the partial unique index allowing one active job per match.
No existing row has metadata, because nothing decoded video before it.

It also moves every `calibration_required` match back to `uploaded`. That status
now promises decoded metadata, which none of the matches backfilled by 0003 has
-- their "finished" job only fingerprinted the file. It enqueues nothing: those
matches show an **Extract metadata** action and wait for someone to use it. The
downgrade drops the columns and index and leaves those statuses `uploaded`,
which is valid at 0003 too.

## Dependencies and Processes

```bash
docker compose up -d postgres redis     # the usual case
docker compose --profile app up -d      # ...plus migrate, api, worker and web
docker compose down                     # stop; add -v to discard the data
```

The `app` profile runs the migrations itself: a one-shot `migrate` service
applies them, and `api` and `worker` wait for it to exit successfully. Nothing
needs running by hand on the host.

Note that the two paths keep their uploads in different places. A host process
writes `data/uploads`; the containers share a Docker volume. A video uploaded
one way is a `missing_video_file` job the other way.

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

Video decoding is tested without the network or an ffmpeg binary.
`pickleball_ml.video.fixtures.write_test_video` writes a few-kilobyte clip with
OpenCV, byte-for-byte deterministic, and can tag it with a rotation by patching
the track header's display matrix -- which is where a phone records it. The
worker's `stored_video` fixture stores one of these (64x48, tagged 90 degrees)
and a test decodes it end to end, expecting 48x64. The processor's own tests
mock `read_metadata` where the processor looks it up
(`pickleball_worker.processors.read_metadata`). The fake `ftyp` bytes the
upload tests use are, correctly, *not* decodable; one test relies on that.

The integration tests apply the migrations to a throwaway PostgreSQL database,
compare the result against the models (catching a model edited without a new
migration), and roll it back again. They also seed Phase 1 rows at revision
0002 and take them up through 0003 and 0004, back down and up again, checking
every row on the way, and `test_retry_concurrency.py` races eight requests for
one match against the retry endpoint; `test_migration_0003.py` does the same on SQLite in the default
suite.

## Troubleshooting

**Jobs stay `queued` for ever.** Nothing is consuming the queue: start
`uv run pbworker`, or `docker compose --profile app up -d worker`. The status
page says as much rather than pretending something is happening.

**Every job fails with `unreadable_video`.** The file is not a video OpenCV can
decode. The upload's `ftyp` check only looks at the first bytes; the worker is
the real test. The worker log has the decoder's own message.

**The containerised worker fails with `libGL.so.1: cannot open shared object
file`.** The image was built before `libgl1` was added; rebuild with
`docker compose --profile app up -d --build`.

**Every job fails with `missing_video_file`.** The API and the worker resolved
different upload directories. `PICKLEBALL_UPLOAD_DIR` defaults to the relative
`data/uploads`, so a process started from another directory writes somewhere
else. Both log the absolute path they resolved at startup; compare them. This
also happens when a video is uploaded through the host API and a containerised
worker looks for it, or vice versa — the two paths do not share storage.

**`relation "matches" does not exist`, or every API call 500s.** The migrations
have not been applied: `uv run alembic -c apps/api/alembic.ini upgrade head`.
Nothing creates tables at startup on purpose. In the container path the
`migrate` service does this, so check `docker compose logs migrate`.

**`bind: address already in use` on 5432.** Something else, usually a
Homebrew PostgreSQL, already has the port. The compose file publishes 5433 for
exactly this reason, so nothing needs stopping — but a `PICKLEBALL_DATABASE_URL`
pointing at 5432 will reach the other server.

**`PermissionError` writing to `/data/uploads` in Docker.** The named volume was
created by an older image that ran as root, and Docker keeps a volume's
ownership once it exists. `docker compose --profile app down -v` recreates it.

**The browser shows "Could not reach the API".** Either the API is not running,
or its CORS list does not include the page's origin. The default allows
`http://localhost:3000` and `http://127.0.0.1:3000`; a different port needs
`PICKLEBALL_CORS_ORIGINS`. Note that `localhost` and `127.0.0.1` are different
origins to a browser.

**A request returns `Invalid host header` as plain text.** `TrustedHostMiddleware`
rejected the `Host` header, and it answers before the JSON error handlers, so
this one response is not in the usual error shape. Add the host to
`PICKLEBALL_TRUSTED_HOSTS`.

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

Phase 1 is complete; everything below is later-phase work.

**Remaining prerequisite for Phase 2 (court calibration).** The `Match`
ownership model (checkpoint 1) and metadata extraction (checkpoint 2) are done.
Still to build before the calibration UI itself:

- Serving uploaded video back to the browser, which needs its own decisions
  about origin, `Content-Disposition` and `X-Content-Type-Options`; an mp4 that
  is also valid HTML is a stored-XSS vector if it is served from the app's own
  origin. Clicking landmarks on a frame requires this.

Known gaps in what is here:

- **Retries are manual.** A failed extraction is retried by
  `POST .../metadata-jobs`, which adds a new job; nothing retries
  automatically, and the `failed -> queued` transition on an existing job is
  still unused. Once metadata exists there is no way to extract it again.
- **No authentication or per-user authorization.** Every match is visible to
  anyone who can reach the port.
- **Nothing edits a match.** `name` and `recorded_at` are set at upload and
  there is no endpoint to change them.
- **No deletion endpoint**, which `ARCHITECTURE.md` requires for privacy.
- **The size limit bounds what is stored more tightly than what is buffered.**
  An upload declaring an oversized `Content-Length` is refused by middleware
  before the body is read, which covers every ordinary client. A chunked
  request declares no length, and Starlette spools it to a temporary file
  before the route can count bytes -- so nothing oversized is ever *stored*,
  but the disk cost of receiving it has been paid. A deployment should also set
  a body limit at its reverse proxy.
- **A worker killed mid-job leaves its row in `running` for ever.** There is no
  reaper; `JobErrorCode.ABANDONED` is reserved for one and is currently unused.
  The same applies to a crash between the commit and the enqueue, which leaves
  a `queued` row with nothing on the queue. Since checkpoint 2 this also blocks
  the retry endpoint for that match, because the stuck job counts as active;
  the fix is the reaper, not loosening the one-active-job rule.
- **Duration is an estimate.** It is frame count over average frame rate, not
  the container's own duration, which OpenCV does not expose. For variable
  frame-rate video the two can differ slightly.
- **Redis is trusted.** RQ deserializes job metadata with pickle, so a reachable
  Redis is worker code execution. The queue payload is only an opaque job id
  and everything else is re-read from PostgreSQL, so a tampered message can at
  worst re-run a real job -- but Redis must stay on the loopback interface.
