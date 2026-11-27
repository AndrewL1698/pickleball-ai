# Backend

The FastAPI service, the database, the storage layer and the job worker added in
Phase 1, re-parented under `Match` in Phase 2 checkpoint 1, given real video
metadata extraction in checkpoint 2, calibration images and court calibrations
in checkpoints 3 and 4, and model runs with their stored artifacts and the
players job that runs Detect in Phase 3.
The architecture
this is heading towards is in `ARCHITECTURE.md`, and the eventual schema is in
`DATA_MODEL.md`.

## What This Does and Does Not Do

It does: accept a video upload as a new match, store the file, record the match,
its video and a job, queue the job, let a worker claim it, decode the video's
metadata and make stills from it, drive the job through
`queued -> running -> ready` (or `failed`) while the match's status follows, retry
on request, fit and save a court calibration, queue player tracking once the court is
calibrated, run person detection over the video in checkpointed chunks, and expose
enough over HTTP for a frontend to list and inspect matches.

There are two kinds of job (`analysis_jobs.kind`). A `preparation` job, which every
upload gets, runs the worker's `CalibrationPreparationProcessor`: it reads the video's
dimensions, rotation, average frame rate, duration, frame count and codec, writes
stills of it, with `pickleball_ml.video.reader`, and compares those frames for a
moved camera. A `players` job, queued by the first calibration, runs *Detect* (ADR
0001): YOLO person detection, the court gate and ByteTrack, over the steady part of the
video (see Player Detection). It writes image-space detections only. Identify, which
turns them into players in court coordinates, is not here yet.

The preparation job also transcodes an H.264 playback copy of the video
(`pickleball_worker.playback`), which `GET .../playback` streams with range
requests. There is no authentication yet. The web app that drives this API is
described in `FRONTEND.md`.

## Directory Structure

```text
apps/api/                              # package `pickleball_api`
├── alembic.ini                        # migrations config; the DB URL comes from settings
├── Dockerfile                         # shared by the api and worker compose services
├── migrations/
│   ├── env.py                         # reads the URL from settings unless given one
│   └── versions/                      # 0001 tables, 0002 CHECKs, 0003 matches, 0004 metadata, 0005 stills, 0006 calibrations, 0007 camera check, 0008 model runs, 0009 job kind, 0010 run chunks, 0011 playback copy
├── src/pickleball_api/
│   ├── calibration.py                 # fits landmarks through pickleball_ml, or refuses them
│   ├── config.py                      # typed settings, and the extension allowlist
│   ├── db.py                          # engine, session factory, request/worker sessions
│   ├── dependencies.py                # what routes ask for: settings, session, storage, queue
│   ├── errors.py                      # job error codes and their user-safe messages
│   ├── jobs.py                        # the job state machine, match status sync, read queries
│   ├── main.py                        # create_app(): middleware, routers, error handlers
│   ├── models.py                      # Match, Video, AnalysisJob, ReferenceFrame, CourtCalibration, ModelRun, ModelRunChunk
│   ├── queue.py                       # JobQueue interface, RQ and recording implementations
│   ├── run_artifacts.py               # a run's Parquet artifact, or a Detect run's chunks, through Storage
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
│   ├── processors.py                  # VideoProcessor, MetadataProcessor, CalibrationPreparationProcessor
│   ├── playback.py                    # the H.264 playback copy, with the bundled ffmpeg
│   ├── backfill_playback.py           # `pbworker-backfill-playback`: copies for older videos
│   ├── players.py                     # players jobs: Detect, chunk by chunk, resumable
│   ├── job_updates.py                 # a running job's progress and its outcome
│   ├── tasks.py                       # the RQ task, dispatch by job kind, transaction boundaries
│   └── __main__.py                    # `pbworker`
└── tests/
```

Both are uv workspace members alongside `ml/`.

## Dependency Direction

```text
pickleball_ml      imports nothing from this repository
pickleball_api     imports pickleball_ml.court.geometry and .spec, never pickleball_worker
pickleball_worker  imports pickleball_api, pickleball_ml.video and pickleball_ml.players.detect
```

This is what keeps torch and ultralytics out of the API process. Both depend on
`pickleball-ml` **without** its `tracking` extra: the base install is OpenCV, NumPy
and the light libraries the pipeline modules import, while `ultralytics` (and
through it torch) and `lap` are the extra, which the workspace root installs for the
Phase 0 CLI and the native worker. The worker has its own `tracking` extra, which
pulls in `pickleball-ml[tracking]`; the Docker image installs the worker without it.
`pickleball_ml.players.detect` imports ultralytics only inside `load_yolo_backend`,
when a players job starts, so the image contains neither torch nor ultralytics, and
a test on each side asserts that importing it loads neither. A worker without them
fails players jobs with `tracking_unavailable`.

The API imports the geometry so that there is one homography implementation, and
so a person gets their calibration's error straight back. Fitting a dozen points
takes no meaningful time, unlike anything that decodes video, which stays in the
worker. The cost is OpenCV in the API process. The image already has it, since the
API and worker share a Dockerfile.

The API
enqueues by dotted path (`pickleball_worker.tasks.run_analysis_job`) rather than
by importing the function, so the edge really does point one way.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/matches` | Multipart upload (`file`). Creates a `Match`, its `Video` and a queued `AnalysisJob`. `201` |
| `GET` | `/api/matches` | Matches newest first, each with its video and latest job. `limit`, `offset` |
| `GET` | `/api/matches/{match_id}` | One match, its video and metadata, its latest job and every job attempt. `404` if unknown |
| `POST` | `/api/matches/{match_id}/metadata-jobs` | Queue a job for a match with no metadata. `202` with the match; `409` if refused; `503` if it cannot be queued |
| `POST` | `/api/matches/{match_id}/reference-frame-jobs` | Queue a job for a match with metadata but no calibration images. Answers as above |
| `GET` | `/api/matches/{match_id}/reference-frames/{frame_id}` | One calibration image, as `image/jpeg`. `404` if unknown, under another match, or gone from storage |
| `GET` | `/api/matches/{match_id}/playback` | The H.264 playback copy, as `video/mp4`, with range requests (`206`, or `416` for a range past the end; Starlette answers `416` and a malformed `400` in plain text, not the error shape below). `404` if the match is unknown, has no copy yet, or the copy is gone from storage |
| `POST` | `/api/matches/{match_id}/calibrations` | Fit and save a calibration, and queue a players job for a match never detected. `201` with the match; `409` if refused; `422` with a sentence if the landmarks cannot make a court |
| `POST` | `/api/matches/{match_id}/players-jobs` | Queue a players job that continues an unfinished Detect run, or starts one. `202` with the match; `409` without a calibration, while a job is active, or once Detect has finished; `503` if it cannot be queued |
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
  "latest_job": { "id": "…", "match_id": "…", "kind": "preparation", "status": "ready", "stage": "metadata_ready", "…": "…" },
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

`MatchDetail` also carries `reference_frames` (the background first), `calibration`
(the one in force, or null), and `can_extract_reference_frames`, `can_calibrate` and
`can_start_players_job`, which work like `can_extract_metadata`. A job's `kind` is
`preparation` or `players`. A calibration has:

```json
{
  "id": "…", "reference_frame_id": "…", "source": "manual",
  "image_points": { "near_left_kitchen": [550.0, 633.0] },
  "court_points": { "near_left_kitchen": [-10.0, -7.0] },
  "homography": [[…], […], […]],
  "reprojection_error_ft": { "near_left_kitchen": 0.08 },
  "mean_reprojection_error_ft": 0.1, "max_reprojection_error_ft": 0.2,
  "created_at": "…",
  "projected_lines": { "near_baseline": [[76.2, 1020.1], [1794.8, 1024.9]] },
  "far_baseline_ft_per_pixel": 0.49,
  "warnings": []
}
```

`homography` maps image pixels to court feet, and only means anything for a point
on the ground. `projected_lines`, `far_baseline_ft_per_pixel` and `warnings` are worked
out when it is read: `projected_lines` is each court line's two ends in pixels, for an
overlay, `far_baseline_ft_per_pixel` is the most one image pixel can be worth along the
far baseline, or null if that line cannot be projected, and `warnings` is fixed sentences
for anything to review.

`video.camera_jumps` lists where sampled frames show the camera moved, each with
`from_seconds`, `to_seconds` and `shift_px`. A move is either a bump between two
neighbouring samples or creep: small steps that add up past the same 20 px since the
camera last came to rest. An empty list means checked with no move found. Null means not
checked: the video was processed before the check existed, a requested frame did not
decode, a pair of frames could not be compared, or the check itself failed (that is
logged and does not fail the job). A move and back between two samples would not show.

`video.playback_available` says whether `GET .../playback` has a copy to stream. It is
false until the preparation job has made one, and stays false if the transcode failed
(that is logged and does not fail the job) until `pbworker-backfill-playback` retries
it. The response never names where the copy is stored.

`MatchSummary` (each item of `GET /api/matches`) is the same without `jobs`, the
`can_*` flags, `reference_frames` and `calibration`.
`video` is nullable in the schema because the database permits a match without
one, but the upload path never produces it.
`storage_key` appears in no response, and a test checks the OpenAPI document
for it.

Upload rejections: `415` for an extension outside the allowlist or bytes that
are not an ISO base-media file, `413` past the size limit, `507` when the disk
has no room, `503` when the job cannot be queued.

## Data Model

Six tables. `matches` is the parent:

| Column | Notes |
|---|---|
| `id` | UUID. Public, so it is random rather than sequential |
| `name` | Defaults to the upload's filename without its extension, whitespace collapsed; `Untitled match` if nothing is left |
| `recorded_at` | When the game was played. Nullable, and nothing sets it yet |
| `status` | `uploaded`, `processing`, `calibration_required`, `court_ready`, `failed` |
| `created_at` | `timestamptz`, set in Python |
| `current_identify_run_id` | FK to `model_runs`, nullable. The Identify run whose player tracks the match shows |

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
| `camera_jumps` | JSON, nullable. Where sampled frames show the camera moved. Empty means none found, and null means not checked |
| `playback_storage_key` | `VARCHAR(512)`, nullable, unique. The H.264 playback copy. Null until made, and after a failed transcode. Never in a response |

`analysis_jobs`: `id`, `match_id` (FK, `ON DELETE CASCADE`), `kind` (`preparation` or
`players`, default `preparation`), `status`, `stage`,
`progress` (0.0-1.0, with a CHECK), `error_code`, `error_message`, `created_at`,
`started_at`, `finished_at`. Indexed on `(match_id, created_at)`, which serves
both "every job for this match" and "its latest job". The latest job is the
newest `created_at`, ties broken by `id`. A partial unique index,
`ux_analysis_jobs_one_active_per_match`, allows at most one `queued` or
`running` job per match, of any kind; job history is unaffected.

`reference_frames`: `id`, `match_id` (FK, `ON DELETE CASCADE`), `ordinal` (display
order, unique within a match), `kind` (`background` or `sample`), `position_seconds`
(approximate; a CHECK requires it for a `sample` and forbids it for a `background`),
`source_frame_count`, `storage_key` (unique, never returned), `width`, `height`,
`byte_size`, `created_at`.

`court_calibrations`: `id`, `match_id` and `reference_frame_id` (FKs, `ON DELETE
CASCADE`), `source` (`manual`), `image_points`, `court_points`, `homography`,
`reprojection_error_ft` (each JSON), `mean_reprojection_error_ft`,
`max_reprojection_error_ft`, `created_at`. Indexed on `(match_id, created_at)`. Rows
are only added: the newest is in force, so a recalibration destroys nothing.

`model_runs`: `id`, `match_id` (FK, `ON DELETE CASCADE`), `stage` (`detect` or
`identify`), `model_version`, `config_json`, `config_hash` (SHA-256 of the config,
with sorted keys, together with `calibration_id`, from `models.hash_config`),
`calibration_id` (FK to `court_calibrations`, `NOT NULL`, not cascaded),
`artifact_storage_key` (nullable until the artifact is written, and always null for a
Detect run, whose output is its chunks; unique, never returned), `started_at`,
`completed_at` (null while running; for Detect, set with its last chunk), `parent_run_id` (FK to
`model_runs`). Both stages need a calibration (ADR 0001): Detect uses it to gate
people before ByteTrack, and Identify uses it to project into court feet. The
calibration is stored only in its column. Hashing it keeps a run made under an old
calibration from looking reusable after a recalibration. A CHECK requires an `identify`
run to have a parent and a `detect` run to have none, and another requires
`completed_at >= started_at`. Indexed on `(match_id, started_at)`. Make runs with
`ModelRun.start`, which takes a saved calibration of the same match and refuses an
Identify run whose parent is not a Detect run of the same match. `Match.current_identify_run` accepts only an Identify run of
that match. Runs are only added. Neither foreign key into `model_runs` cascades, so a run
that a match shows or another run read cannot be deleted on its own. Nor can a
calibration that a run used, or the reference frame under it. Deleting the match
removes all of them.

`model_run_chunks`: `id`, `run_id` (FK to `model_runs`, `ON DELETE CASCADE`), `index`
(0, 1, … from the start of the run's steady part; `>= 0`), `start_seconds` and
`end_seconds` (the video the chunk covers; a CHECK keeps the end after the start),
`storage_key` (unique, never returned), `completed_at`. `(run_id, index)` is unique. A
Detect run's output is its chunks, each a Parquet object committed with its row, so a
retry finds the first unfinished chunk by counting them.

`CourtCalibration.reference_frame` exists so the ORM orders its writes. Without it,
deleting a match through the ORM could delete a frame before the calibration and run
that depend on it.

`matches.current_identify_run_id` and `model_runs.match_id` point at each other. The
ORM gets through that cycle with `post_update` on `Match.current_identify_run`.

`status` and `stage` are stored as `VARCHAR` plus a CHECK constraint (added in
migration `0002`) rather than native PostgreSQL enums, so that adding a member
does not need a type migration and so the same migration runs on the SQLite
database the tests use.

`matches.status` follows the same pattern, with its CHECK created by `0003`.

### Relationship to `DATA_MODEL.md`

`DATA_MODEL.md` describes `Match` owning a `VideoAsset`, a `ProcessingJob` and a
`CourtCalibration`, and that is now the shape here. `Video` is that `VideoAsset`, with its decoded
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

That table is for `preparation` jobs. A `players` job never moves its match: it runs
on a calibrated match, which stays `court_ready` whether tracking is queued, running,
finished or failed. Its progress, its failure and the stage it reached live on the
job. No new match status was added for tracking.

A new upload starts at `uploaded`. There is no `ready`, on purpose. Saving a
calibration sets `court_ready`, in the same commit, and that is the only change to
a match's status that does not come from a job. Once a match has images a
preparation job cannot be started for it, so nothing moves a calibrated match to
`failed`.

`calibration_required` also means "metadata extracted", and for any job finished
since checkpoint 3 "calibration images exist": the worker saves both in the same
commit as the `ready` transition that sets it.

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

### Getting Calibration Images

`POST /api/matches/{match_id}/reference-frame-jobs` covers a match with metadata
and no images: one processed before they existed, or whose attempt failed. It is
the same job an upload gets, so it reads the metadata again as well. It is refused
with `409` when the match has no video, no metadata (`metadata-jobs` is the way in
there), images already, or an active job. Nothing is enqueued from a migration.
Concurrency, queue failure and history work as in Retrying Extraction.

### Saving a Calibration

`POST /api/matches/{match_id}/calibrations` takes `reference_frame_id` and
`image_points`, keyed by landmark name, in that image's pixels. The match row is
locked while it is checked. `409` when the match has no images or a preparation job is
active, and `422` with a fixed sentence when:

- the image belongs to another match
- there are fewer than four landmarks, or one is outside the image
- the fit is underdetermined, as when all the landmarks but one are on one line
- left and right are swapped. The camera's view of the court corners would run
  clockwise, which no camera above the court can produce. This has to be its own
  check because a mirrored court fits just as well, so no error can reveal it.

A calibration may be saved while a players job is queued or running. That Detect run
keeps the calibration it recorded, and Identify uses the newest (ADR 0001).

Saving a calibration queues a players job, committed with it, when Detect has never
run on the match and no job is active. Saving again while that job is queued or
running queues nothing, and so does any later recalibration: a recalibration never
re-detects. A match whose Detect run a failed job left unfinished is continued with
`POST .../players-jobs` instead. If the queue is down the players job is failed with
`enqueue_failed` but the calibration stands and the answer is still `201`;
`can_start_players_job` then offers the retry.

A calibration that fits badly is saved and flagged rather than refused, because a
person can look at the overlay and decide. `warnings` covers an error past 0.5 ft
mean or 1.0 ft max (the worst careful Phase 0 calibration was 0.35 and 0.81), four
landmarks, whose fit is always exact, and landmarks on only one half of the court.

### Starting Player Tracking

`POST /api/matches/{match_id}/players-jobs` queues a players job, with
`PICKLEBALL_PLAYERS_JOB_TIMEOUT_SECONDS` as its time limit. It is accepted when the
match has a calibration, no job is active, and no Detect run is complete
(`Match.players_job_refusal()`, which `can_start_players_job` mirrors). The job
continues the match's unfinished Detect run, with that run's own calibration and
configuration rather than the newest calibration, or starts one if there is none.
There is no button for it yet, and nothing reaps a players job whose worker was
killed (see What Is Left).

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

The stage names follow the ladder in `ARCHITECTURE.md`. A preparation job reaches
`metadata_ready` and stops; it stays at `ingested` while it runs, and only the task
layer sets `metadata_ready`, once the result is saved. A players job starts at
`court_ready`, the stage its match already reached when a person calibrated it,
reports progress there, and finishes at `players_ready`.

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

### Run artifacts

A model run's output is one Parquet file, written and read with
`pickleball_api.run_artifacts`. `write_run_artifact(storage, run, frame)` stores the
DataFrame (without its index) under a fresh random `.parquet` key and sets
`run.artifact_storage_key`. The caller commits, using `discard_on_error` if that
commit can fail. A run that already has an artifact is refused with
`ArtifactAlreadyWritten`, because runs are never overwritten. Both errors derive from
`RunArtifactError`, not `StorageError`, because they are about the run, not the store. `read_run_artifact`
reads it back through `local_path`. It raises `NoArtifact` for a run that never wrote
one, and `ObjectNotFound` if the file is gone.

A Detect run writes chunks instead, with `write_detect_chunk(storage, run, index=…,
start_seconds=…, end_seconds=…, frame=…, is_last=…)`: one Parquet object under a fresh
key and one `ModelRunChunk` row, which the caller commits inside `discard_on_error`. It
refuses an Identify run (`NotADetectRun`), a chunk out of order (`ChunkOutOfOrder`; the
next is always `len(run.chunks)`) and a complete run (`RunAlreadyComplete`). The last
chunk sets the run's `completed_at`. `read_detect_chunks(storage, run)` returns the
finished chunks in order as one DataFrame. It works on an unfinished run too, and gives
an empty DataFrame for a run with none.

## The Worker

`pbworker` runs an RQ worker against the configured queue. The task takes a job
id and nothing else: everything else is read from PostgreSQL (job -> match ->
video), so the queue is not a channel through which paths or configuration can
be injected. A job whose match has no video is failed with
`missing_video_file` during the claim, without ever reaching `running`.

### Player Detection

`run_analysis_job` reads the job's `kind` first and hands a players job to
`pickleball_worker.players.run_players_job`. The claim locks the job, moves it to
`running` at `court_ready`, and reads in the same transaction which Detect run to
continue or which calibration to start one with. Then:

1. **The steady part.** One homography fits one camera position. With
   `videos.camera_jumps`, the video splits into steady parts `[0, first from]`,
   `[to, next from]`, …, `[last to, end]`; the window inside a move belongs to none.
   Detect covers the part holding the calibration's reference frame, a frame on a
   boundary belonging to the part it bounds (`pickleball_ml.video.steady_part`). A
   background still on a moved camera fits no part, and nor does a still taken while
   the camera was moving: the job fails with `camera_moved`, asking for a calibration
   on a single still. A video never checked (`camera_jumps` null) is tracked whole,
   recorded as `steadiness: "unchecked"`.
2. **The detector.** `load_yolo_backend` loads `PICKLEBALL_PLAYER_MODEL_PATH` on `mps`,
   else `cuda`, else `cpu`. No model file, or no ultralytics/torch, fails the job with
   `tracking_unavailable`.
3. **The run.** A new Detect run is committed before any work, with `model_version`
   naming the model file and its SHA-256 and `config_json` holding `imgsz`, `conf`,
   the gate margins, ByteTrack's settings, the rate, the chunk length, and the steady
   part: its range, its `excluded` parts (each with the `moved_at_seconds` a page
   would show as "the camera moved at 6:08") and its `steadiness`, and the `runtime`:
   device and ultralytics and torch versions (`DetectRunConfig`). So `config_hash`
   covers all of it.
4. **The chunks.** The video is decoded once, in order. Every frame is grabbed, but
   only about 15 a second are converted and detected: the first frame at or after each
   1/15 s tick (`RateSampler`), so 30 fps, 59.94 fps and variable-rate video all give
   the same rate. Each 5-minute chunk of the steady part is committed, object and row
   together, before the next one starts. ByteTrack starts afresh at every chunk; its
   lost-track buffer is two seconds of processed frames (30 at 15 Hz), and track ids
   are offset by `chunk index * 1,000,000` so they stay unique.
5. **Progress** is the share of the steady part done, reported at `court_ready`; the
   job finishes `ready` at `players_ready`.

Each detection row has `frame_number`, `timestamp_ms`, `track_id` (-1 when the person
is outside the gate or not tracked), the box, `confidence`, the ground-contact point in
image pixels (`image_x`, `image_y`, the bottom centre of the box), `truncated` (the box
touches the bottom of the frame), `in_gate`, and a clothing-colour `appearance`
descriptor for gated people. There are no court coordinates: Identify derives those
from the current calibration (ADR 0001).

**Resuming.** A players job on a match with an unfinished Detect run continues it from
its first unfinished chunk, with the run's own calibration, configuration and steady
part, and skips the frames before it without converting them. Because the tracker
restarts at each chunk and whether a frame is sampled depends only on timestamps, the
result is identical to a run that was never interrupted; a test kills one partway and
compares. A worker whose model file has a different SHA-256 from the run's refuses to
continue it (`model_changed`). A different device or library version only logs a
warning, since the output should match to within a pixel. A job for a match whose Detect run is complete
finishes at once without detecting anything.

### Calibration Images

`CalibrationPreparationProcessor` runs `MetadataProcessor`, then seeks to 25 points
spread evenly through the video, using the duration it just read. The per-pixel
median of whatever decodes is the background, in which moving players vanish, and
the frames nearest the quarter points are kept as stills, for a video whose camera
moved and whose background is therefore smeared. All are JPEGs in display
orientation, at the video's own size, so a pixel on one is a pixel in every later
image coordinate. A seek is approximate, which is why a still records a position
and not a frame number: the homography belongs to the camera, not to a moment.

A processor cannot reach the database, so it writes the files and returns their keys,
and the task layer records them in the same commit as the metadata and the `ready`
transition. If that commit fails, or the job is no longer this attempt's to finish,
the task layer deletes them, and a write that fails partway deletes what it wrote.
A video with metadata but not one decodable frame fails with `unreadable_video`.

Holding 25 frames takes about 155 MB at 1080p and 620 MB at 4K, and the median about
as much again. Those are calculated, not measured. A 14:44 1080p clip took 4.2 s in
total on an M4.

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
| `PICKLEBALL_PLAYERS_JOB_TIMEOUT_SECONDS` | `14400` | Players jobs' timeout. Tracking takes about as long as the match |
| `PICKLEBALL_PLAYER_MODEL_PATH` | `weights/yolo26m.pt` | The worker's person detector, relative to where it starts |
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
`JobStage`, `JobKind`, `MatchStatus`, `ReferenceFrameKind`, `CalibrationSource` or
`ModelRunStage` needs its migration written by hand; `0002` is the pattern.

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

### Migrations 0005 and 0006: images and calibrations

Each adds one table and touches no existing row. Nothing is enqueued, so a match
that already has metadata shows a **Make calibration images** action. Downgrading
0006 loses every calibration, and moves `court_ready` matches back to
`calibration_required`, since that status promises a calibration exists. Downgrading
0005 forgets the images, but their files stay in storage until something removes
them.

### Migration 0007: the camera check

Adds nullable `videos.camera_jumps` and enqueues nothing, so every existing video stays
null, meaning not checked, unless it is uploaded again. Reference-frame jobs only run for
a match with no images, so they will not fill it in. Downgrading forgets the results.

### Migration 0008: model runs

Adds `model_runs`, with a required `calibration_id`, and nullable
`matches.current_identify_run_id`, and enqueues nothing. Every existing match shows no run. On SQLite the new column and its foreign
key are added with one plain `ALTER TABLE ... ADD COLUMN`, not batch mode, because
rebuilding `matches` with foreign keys on would cascade-delete everything the match
owns. `test_migration_0008.py` checks that nothing is lost. Downgrading forgets every
run, and the artifacts stay in storage until something removes them.

### Migration 0009: job kind

Adds `analysis_jobs.kind`, `NOT NULL` with a server default of `preparation` and a CHECK,
so every existing job is a preparation job. On SQLite it is one plain `ALTER TABLE ...
ADD COLUMN`, as in 0008, so the table and its partial unique index are not rebuilt.
Nothing is queued: an already calibrated match gets player tracking from
`POST .../players-jobs`. Downgrading drops the column.

### Migration 0010: Detect run chunks

Adds `model_run_chunks`. Existing runs have none. Downgrading forgets them, and their
objects stay in storage until something removes them.

### Migration 0011: playback copy

Adds nullable, unique `videos.playback_storage_key` in batch mode, as 0007 did; no table
references `videos`, so rebuilding it on SQLite cascades nothing. It makes no copies and
enqueues nothing: `uv run pbworker-backfill-playback` makes them for existing videos.
Downgrading forgets the keys, and the copies stay in storage until something removes them.

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
and tests decode it end to end, expecting metadata and images at 48x64. The processor's own tests
mock `read_metadata` where the processor looks it up
(`pickleball_worker.processors.read_metadata`). The fake `ftyp` bytes the
upload tests use are, correctly, *not* decodable; one test relies on that.
The playback copy is the exception: `test_playback.py` encodes the fixture with
the ffmpeg that `imageio-ffmpeg` bundles, and probes the result with the same
binary (H.264, 48x64, no rotation tag, the source's frame count).

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

A calibration image is served with a fixed `image/jpeg` type, `nosniff`, and
`Content-Security-Policy: default-src 'none'; sandbox`. Its bytes come from the
worker's own JPEG encoder, not from anything uploaded, and the route takes only
ids, so no client value reaches a path.

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

**Video playback.** The API serves only the playback copy the worker encoded,
never the upload itself, with a fixed `video/mp4` type, `nosniff` and a sandbox CSP,
so an upload that is also valid HTML cannot run from the app's origin. The web
player is #9. With an object store, the route would redirect to a signed URL
instead of streaming a local file.

Known gaps in what is here:

- **Retries are manual.** A failed extraction is retried by
  `POST .../metadata-jobs`, which adds a new job; nothing retries
  automatically, and the `failed -> queued` transition on an existing job is
  still unused. Once metadata and images exist there is no way to make them again.
- **No authentication or per-user authorization.** Every match is visible to
  anyone who can reach the port.
- **Nothing edits a match.** `name` and `recorded_at` are set at upload and
  there is no endpoint to change them.
- **No deletion endpoint**, which `ARCHITECTURE.md` requires for privacy. Calibration
  images are personal data too, since a still shows the players.
- **Only one camera position is tracked.** One calibration cannot be right on both
  sides of a move, so a players job tracks the steady part holding the calibration's
  image and records the rest as excluded. Nothing tracks the excluded parts yet.
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
  the fix is the reaper, not loosening the one-active-job rule. A killed players job
  is the expensive case: its finished chunks are safe, but nothing can continue the
  run until the stuck job is failed.
- **Duration is an estimate.** It is frame count over average frame rate, not
  the container's own duration, which OpenCV does not expose. For variable
  frame-rate video the two can differ slightly.
- **Redis is trusted.** RQ deserializes job metadata with pickle, so a reachable
  Redis is worker code execution. The queue payload is only an opaque job id
  and everything else is re-read from PostgreSQL, so a tampered message can at
  worst re-run a real job -- but Redis must stay on the loopback interface.
