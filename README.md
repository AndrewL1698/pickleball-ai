# Pickleball Match Intelligence

A full-stack computer-vision and machine-learning project for turning ordinary smartphone pickleball footage into structured match data, interactive analytics, and tactical insights.

## Vision

The application should eventually let a player upload a match and receive:

- automated rally segmentation
- player and ball tracking
- court-position heatmaps
- movement and kitchen-transition statistics
- shot placement and shot-selection analytics
- recurring strategic-pattern detection
- evidence-backed coaching insights linked to specific rallies

The project intentionally separates perception from reasoning:

```text
Video
  -> Court calibration
  -> Player tracking
  -> Ball tracking
  -> Rally / hit / shot events
  -> Structured match representation
  -> Analytics
  -> Coaching explanation
```

## Current Status

Phase 0a (CV prototype) is implemented as a command-line pipeline for one test video:

```text
video -> manual court calibration -> player tracks -> homography -> animated top-down court
```

Phase 0b extends that to several recordings at once: an experiment manifest of
sampled windows, camera comparison across four fixed-camera matches, measured
player-tracking metrics, and a first ball-tracking baseline. Results and the
exit-criterion assessment are in `docs/PHASE_0B_RESULTS.md`.

Phase 1 (full-stack skeleton) is complete. All three of its checkpoints are
done, and it passed its exit criterion: a video can be uploaded and watched
through a background processing job, in the browser.

- A FastAPI service that accepts a video upload, records it in PostgreSQL,
  stores the file behind a storage interface, and queues an analysis job that a
  Redis/RQ worker picks up and drives to completion (`docs/BACKEND.md`).
- A Next.js app for uploading a video, listing what has been uploaded, and
  following a job through its states (`docs/FRONTEND.md`).
- Integration and hardening: the whole stack starts from a clean checkout,
  natively or with `docker compose --profile app`, with migrations applied by
  the stack rather than by hand.

The processing itself is a **placeholder**: it fingerprints the uploaded file
rather than analysing it, so the plumbing could be finished before the
expensive computer vision is wired in. No video is decoded, and there is no
`Match` entity — the app says "Videos" because that is what it stores. The web
app states this on the page rather than presenting a finished-looking result.

Phase 2 (court calibration) is next, and has to introduce the `Match`
ownership model, real metadata extraction and secure video playback before the
calibration UI itself. See `docs/ROADMAP.md`.

## Development Setup

Prerequisites:

- [uv](https://docs.astral.sh/uv/) — installs Python 3.13 automatically
- [Node.js](https://nodejs.org/) 20.9 or newer (24 is what the web image uses), for the frontend
- Docker Desktop, for PostgreSQL and Redis

ML code runs natively; on Apple Silicon it uses the PyTorch `mps` device.

Run every backend command from the repository root: the upload directory and
`.env` are resolved relative to the working directory, so running from
elsewhere quietly uses a different store.

```bash
uv sync                 # create .venv and install the workspace
uv run pytest           # unit tests (no database, Redis, or video needed)
uv run ruff check .     # lint
uv run mypy             # type check
```

Tests that need a live PostgreSQL and Redis are marked and skipped by default:

```bash
docker compose up -d postgres redis
uv run pytest -m integration
```

Pretrained detector weights are downloaded into `weights/` (gitignored) on first use.

Ball tracking uses the released WASB tennis model, which is not downloaded
automatically. Fetch it once into `weights/wasb/` (6 MB, MIT-licensed code and
published weights from [WASB-SBDT](https://github.com/nttcom/WASB-SBDT)):

```bash
uvx gdown 14AeyIOCQ2UaQmbZLNQJa1H_eSwxUXk7z -O weights/wasb/wasb_tennis_best.pth.tar
```

## Running the Backend (Phase 1)

The API and the worker are Python packages in the same uv workspace as `ml/`:
`apps/api` (`pickleball_api`) and `workers/video_processor` (`pickleball_worker`).
PostgreSQL and Redis run in Docker; the Python processes run natively, because
the worker will need the GPU once real processing lands and Docker on macOS
cannot reach it.

```bash
cp .env.example .env              # defaults match the compose file; no secrets in it
docker compose up -d postgres redis
uv run alembic -c apps/api/alembic.ini upgrade head
```

Then, in two terminals:

```bash
uv run pbapi                      # http://127.0.0.1:8000, --reload while developing
uv run pbworker                   # consumes the analysis queue; --burst to exit when empty
```

Upload a video and watch the job run:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready

# Upload any mp4/mov/m4v. Returns the video, its id, and the job created for it.
curl -s -X POST http://127.0.0.1:8000/api/videos -F "file=@/path/to/your/match.mp4"

curl -s http://127.0.0.1:8000/api/videos
curl -s http://127.0.0.1:8000/api/videos/<video_id>
curl -s http://127.0.0.1:8000/api/jobs/<job_id>
```

The job moves `queued -> running -> ready`, or `-> failed` with a code and a
short message. Interactive API docs are at http://127.0.0.1:8000/docs.

Uploads are written to `data/uploads/` (gitignored) under a generated key, never
under the uploaded filename. Every environment variable, the full endpoint list,
the migration commands and the current limitations are in `docs/BACKEND.md`.

## Running the Frontend (Phase 1)

The web app is a Next.js application in `apps/web`. It is a browser client for
the API above, so start the backend first.

```bash
cd apps/web
npm install
cp .env.example .env.local      # defaults already point at a local API
npm run dev                     # http://localhost:3000
```

Three pages: upload a video, see everything uploaded, and follow one video's
processing job. The job page polls while the job is queued or running and stops
once it is ready or failed.

Without `uv run pbworker` running, an upload stays `queued` for ever: nothing
else consumes the queue. The page says as much.

```bash
npm test                        # hermetic; needs no API and no network
npm run lint
npm run typecheck
npm run build
npm run check:contract          # needs the API up; catches type drift
```

Everything in containers instead, including the web app. The profile brings up
five services — PostgreSQL, Redis, a one-shot `migrate` job, the API, the worker
and the web app — and applies the migrations itself:

```bash
docker compose --profile app up -d --build
```

The two paths keep uploads in different places: a host process writes
`data/uploads`, the containers share a Docker volume.

Full details, environment variables, the polling design and the current
limitations are in `docs/FRONTEND.md`.

## Running the Phase 0 Pipeline

Each stage writes to `data/processed/<video_name>/` and the next stage reads from there, so stages can be rerun independently.

```bash
V=data/raw/testclip.mp4

# 1. Video metadata -> metadata.json
uv run pbml metadata $V

# 2. Manual court calibration -> calibration.json + calibration_overlay.png
#    Opens a window: click each landmark in the order prompted
#    (s = skip hidden landmark, u = undo, enter = finish, esc = abort).
#    --background builds a median image over a time window, removing moving players.
uv run pbml calibrate $V --background 120 300
#    Or non-interactively from saved clicks:
uv run pbml calibrate $V --background 120 300 --points data/annotations/testclip/calibration_points.json

# 3. Person detection, court gating, ByteTrack over a time window (seconds)
#    -> detections_raw.parquet. Needs the calibration: detections whose feet land
#    off the court never reach the tracker. No need to cut the video: frame
#    numbers always refer to the source file.
uv run pbml track $V --start 120 --end 300

# 4. Resolve track fragments into four persistent players (P1-P2 near, P3-P4 far)
#    -> players_court.parquet. Takes seconds, so it can be re-tuned without re-tracking.
uv run pbml identify $V

# 5. Side-by-side debug video (frame + animated top-down court) -> topdown.mp4
#    Colors are player identities; "P2?" marks a low-confidence identity.
uv run pbml render $V

# 6. Identity accuracy against labeled person boxes
uv run pbml evaluate $V --labels data/annotations/testclip/identity_samples.json
```

Check `calibration_overlay.png` before running later stages: the red projected lines should sit on the painted court lines. Landmark definitions and the court coordinate system are in `docs/ARCHITECTURE.md`.

If the teams change ends inside the processed window, pass each changeover time
so identities survive it:

```bash
uv run pbml identify $V --end-switch 950
```

## Running a Multi-Window Experiment (Phase 0b)

One video is not enough evidence. An experiment manifest lists the source clips,
the stretches over which each camera does not move, and the windows to process,
so several windows of several matches can be run, resumed, and compared without
overwriting each other. `experiments/phase0b.json` is the Phase 0b manifest.

```bash
# Everything: metadata, calibration, camera characteristics, tracking, identity, ball
uv run pbml experiment run experiments/phase0b.json

# One window, or one stage, or a 20-second smoke test into <window>_smoke
uv run pbml experiment run experiments/phase0b.json --window buzz_a
uv run pbml experiment run experiments/phase0b.json --stage track --window buzz_a
uv run pbml experiment run experiments/phase0b.json --smoke 20

# Debug videos (top-down players, and the ball with its trail)
uv run pbml experiment run experiments/phase0b.json --stage render --stage ball_render

# Collect every window record and metric into one summary
uv run pbml experiment summary experiments/phase0b.json
```

Each window writes to `data/processed/<clip>/<window>/`, with the same file names
as the single-video pipeline plus `window_run.json`: the exact frame range,
source metadata, calibration used, stage configs, model versions, runtimes and
status. Stages are skipped when their inputs and configuration are unchanged, so
an interrupted run resumes; `--force <stage>` (or `--force all`) overrides that.

A camera that is bumped mid-match needs a second calibration: list both stretches
as separate `segments` in the manifest and point each window at the right one. A
window that crosses a camera move is rejected rather than silently mis-calibrated.

## Labeling and Evaluation

Metrics need labels. Both tools export sampled frames and a template to fill in,
and the filled files live in `data/annotations/<clip>/` (tracked, JSON only).

```bash
# Frames with numbered detection boxes, plus enlarged crops; predicted IDs are hidden
uv run pbml label-players experiments/phase0b.json --window buzz_a --every-seconds 5

# Ball centres: click tool with a magnifier (a: absent, u: unsure, b: back)
uv run pbml label-ball experiments/phase0b.json --window buzz_a
# ... or export the frames and fill the template by hand
uv run pbml label-ball experiments/phase0b.json --window buzz_a --export
```

Point a window's `player_labels` / `ball_labels` at the filled files and the
`evaluate` stage reports coverage, identity accuracy, ID switches, ball
coverage, and pixel error at several tolerances, split into tuning and held-out
windows.

Label honestly or the metrics are worthless:

- Name people by what they are wearing, never by the predicted ID. The player
  export deliberately hides predictions.
- List every court player visible in a frame. A player you leave out counts as
  "not visible", which silently inflates coverage.
- If you cannot tell two players apart in a frame, mark the frame `"skip": true`
  rather than guessing. Guesses show up as tracker errors that are really yours.
- For the ball, `absent` is a claim that the ball is not visible, not that you
  did not find it; use `unsure` when you are not certain.

## Initial Recording Constraints

MVP footage should be:

- doubles pickleball
- landscape video
- recorded from a fixed smartphone on a tripod or mount
- main (1x) lens; ultra-wide (0.5x) distortion breaks court calibration
- preferably behind a baseline, elevated if possible
- full court visible, including both near baseline corners
- camera stationary for the entire match

Recommended settings: 1080p at 60 fps (30 fps is acceptable), HDR video off, no Action or Cinematic mode.

These constraints make the ML problem dramatically more tractable.

## Test Footage

Put source videos in `data/raw/`, for example `data/raw/test_match_01.mov`. Videos, `data/raw/`, and `data/processed/` are gitignored and must never be committed.

For the Phase 0 prototype, a trimmed 1-3 minute clip containing several full rallies is ideal. Pipeline outputs will be written to `data/processed/<video_name>/`.

Small hand-authored annotations are tracked in `data/annotations/<video_name>/` as JSON only (anything else there is ignored):

- `calibration_points.json`: clicked court landmarks for `pbml calibrate --points`
- `identity_samples.json`: labeled player boxes for `pbml evaluate`

The annotations for `testclip` only make sense together with that source video, which is not in the repository.

## Planned Stack

Frontend:
- Next.js
- React
- TypeScript

Backend:
- Python 3.13 (uv)
- FastAPI
- PostgreSQL

ML / CV:
- PyTorch
- OpenCV
- YOLO
- ByteTrack or BoT-SORT
- TrackNet-style ball tracking

Infrastructure:
- Docker
- Redis + background job worker
- S3-compatible object storage

## MVP

The first usable version should:

1. Upload a match.
2. Store and queue it for processing.
3. Calibrate the court.
4. Detect and track all four players.
5. Transform player coordinates to a top-down court.
6. Track the ball well enough to identify active rallies.
7. Display an interactive rally timeline.
8. Produce basic movement/position analytics.
9. Allow users to correct predictions.

## Why Human Correction Matters

Sports CV will make mistakes. Instead of pretending otherwise, this project treats correction as a feature.

Corrections improve:

- trust in statistics
- usability of early models
- future training datasets
- model evaluation

The system should preserve both model predictions and user corrections.

## Roadmap

See `docs/ROADMAP.md`.

## Architecture

See `docs/ARCHITECTURE.md`.

## ML Plan

See `docs/ML_PIPELINE.md`.

## Data Model

See `docs/DATA_MODEL.md`.

## Backend

See `docs/BACKEND.md`.

## Frontend

See `docs/FRONTEND.md`.

## Project Scope

See `docs/PROJECT_SCOPE.md`.

## Development Instructions

See `CLAUDE.md`.
