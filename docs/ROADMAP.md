# Project Roadmap

## Phase 0 - Research and Baselines

Goal: prove that the major CV components are feasible before building a large app. This phase is CLI-only: no database, queue, API, or web app.

### 0a - Immediate milestone (one test video)

- repository setup (uv workspace, pytest, ruff)
- video metadata extraction
- CLI manual court calibration + homography + court-line overlay check
- pretrained person detector + ByteTrack
- filter to the four court players and project them to court coordinates
- side-by-side debug video: original frame + animated top-down court

Exit criteria:

The test clip produces a top-down animation where the four players' positions visibly match the video.

Status (2026-09-14): done. Implemented as the `pbml` CLI (see README). Results on `testclip.mp4`, window 2:00-5:00:

- Footage: corner-mounted, low camera rather than behind a baseline; far half foreshortened and seen through the net.
- Calibration: 11 landmarks (far_left_kitchen hidden by the net post), mean reprojection error 0.28 ft, max 0.66 ft (far kitchen points).
- Detector: YOLO26m at 1280 px + ByteTrack, stride 2 (30 fps effective), ~8 frames/s on an M4.
- 4 players selected in 74% of frames; far half has 2 players in 91%. Most gaps are between rallies, when near players walk out of frame behind the near baseline; some are near partners occluding each other.
- 68 track IDs for 4 players: tracks restart whenever a player leaves the frame or is occluded. No identity linking yet.
- Spot-checked top-down positions visibly match the video.

Revision (2026-09-15): tracks locking onto players on the neighboring court and new IDs after brief tracking loss.

- Detections are gated by court position before tracking, so ByteTrack never sees people on other courts.
- A new `identify` stage splits tracks at impossible jumps, box-size jumps, and abrupt clothing-color changes; selects player fragments with a min-cost flow; and assigns partners with a Viterbi pass over clothing color, motion, and track continuity (`ml/src/pickleball_ml/players/identity.py`).
- Evaluation labels: `data/annotations/testclip/identity_samples.json` (person identity by clothing for sampled player boxes).

| | Original | Revised |
|---|---|---|
| Player identities, 2:00-5:00 | 68 track IDs | 4 players |
| Player rows beyond far baseline + 6 ft | 178 | 58 (mostly real players with feet hidden by the net) |
| Frames with all 4 players | 74% (includes wrong people) | 67% |
| Identity accuracy, 2:00-5:00 (128 labels, also used while debugging) | n/a | 100% of matched samples, 1 missed |
| Identity accuracy, 10:00-12:00 (89 blind labels, never used for tuning, after teams switched ends) | n/a | 100% |

Known limits: far-court positions are still noisy (feet hidden by the net or post push positions several feet too deep); a brief (~0.3 s) grab of a person behind the far baseline remains at 2:14; identity samples are every 5 s, so swaps shorter than that may be unmeasured; the identity step assumes teams do not switch ends inside a processed window; the low-confidence flag is not yet calibrated (no errors left on the labeled set to calibrate against).

### 0b - Broader feasibility

- ~~collect 3-10 representative match clips~~ (four fixed behind-baseline recordings, 14-79 min)
- ~~standardize camera placement~~ (measured recommendation in `docs/PHASE_0B_RESULTS.md`)
- ~~measure player tracking (ID switches, coverage) on sampled frames~~
- ~~link fragmented tracks into four persistent player identities~~ (0a revision)
- ~~handle teams switching ends inside a window~~ (manual changeover input; the window is
  cut at it and players are linked across by clothing plus tracker continuity)
- ~~compare behind-baseline recordings against the corner-camera test clip~~
- ~~test public/open-source racket-sport ball trackers~~ (WASB chosen, alternatives and
  licences recorded)
- ~~manually annotate a short evaluation clip~~ (player and ball labels, tune/test split)
- ~~decide initial ball-tracking baseline~~

Exit criteria:

A short clip can produce visibly reasonable player tracks and at least partial ball tracks.

Status (2026-09-22): **passed**. Nine labeled windows across four matches.

- On the four test windows that influenced no decision, 86.4% of visible players are
  detected and every one of those carries the right identity, with no ID switches in 92
  opportunities.
- Across all five test-designated windows under the final pipeline, 86.1% coverage and
  96.9% identity accuracy with 4 ID switches in 110 opportunities. This is not a clean
  held-out figure: the fifth window, `pro63_hard`, motivated the box-shape fix it is
  measured against.
- `pro63_hard` as a genuine holdout, before it changed anything: 57.7% coverage, 73.3%
  identity accuracy, 14 extra predictions, 4 ID switches.
- Ball tracking is partial: 50% recall at 20 px with 6 px median error where it fires,
  and a 3-4 second gap in every window. The only ball labels are on a tuning window, so
  there is no held-out ball measurement.

Full write-up, per-window metrics and the pass/fail reasoning in
`docs/PHASE_0B_RESULTS.md`.

Carried into later phases:

- Identity across an end switch fails when both teams wear the same kit; the clothing
  descriptor cannot separate partners (0.20 apart on a 0-1 scale, against 0.54-0.69 for
  distinctly dressed teams). Needs user confirmation at each changeover (Phase 7) or a
  stronger appearance model.
- The ball baseline is a cross-sport transfer (tennis weights) and is only partially
  usable: accurate when it fires, but it misses about half of visible balls and reports
  something on a substantial share of ball-free frames. Phase 4 should fine-tune on
  pickleball frames.
- Far-court position noise on low cameras is unresolved; pose-based ground contact was
  not tested.
- Position accuracy in court feet was never measured against a known ground-truth
  position, only calibration reprojection error.
- `pro63_hard` is spent as held-out data. Judging unseen footage again needs a window
  that has not been looked at.

---

## Phase 1 - Full-Stack Skeleton

Build:

- Next.js app
- FastAPI service
- PostgreSQL database
- video list and per-video page
- video upload
- background processing-job abstraction
- processing status UI

Exit criteria:

A user can upload a video and watch its persistent record move through a
mock/background processing job in the browser.

Scope note: this phase was originally written around a "Match page" and a
"match record". `Match` was deliberately deferred — it exists to own
calibrations, players and rallies, none of which exist yet, and an entity with
one field and no children is harder to review than the migration that adds it
later. The skeleton therefore persists `Video` and `AnalysisJob`, and the
exit criterion above is the revised one that was actually met. Phase 2
introduces `Match`.

Status (2026-09-23): checkpoint 1 of 3 done - the backend foundation. A FastAPI
service accepts a multipart upload, records a `Video` and an `AnalysisJob` in
PostgreSQL, stores the file behind a storage interface, and enqueues the job on
Redis; an RQ worker claims it and drives it to `ready` or `failed`. Migrations
are Alembic-only. Details and the environment reference are in `docs/BACKEND.md`.

At that point the processing was a placeholder and there was no web app, so the
exit criterion was not yet met.

Status (2026-09-23): checkpoint 2 of 3 done - the upload and status interface.
A Next.js App Router app (`apps/web`) with three pages: upload a video by
picker or drag-and-drop with validation before and after submission, a list of
everything uploaded with its latest job status, and a per-video status page
that polls only while the job is non-terminal. Typed API client, hermetic
Vitest suite plus an opt-in suite against the running stack. Details in
`docs/FRONTEND.md`.

The collection is called "Videos", not "Matches": `Match` does not exist yet.
The UI states plainly that no analysis is performed.

Status (2026-09-23): checkpoint 3 of 3 done - integration and hardening.
**Phase 1 passes its exit criterion**: a video can be uploaded and watched
through a background processing job, in the browser, from a documented clean
checkout.

Verified end to end, against both the native and the containerised stack:
PostgreSQL and Redis start, Alembic migrations create the schema (never an
application side effect), the API and worker start, the frontend starts, an
upload returns persistent video and job identifiers, the job is observed moving
`queued -> running -> ready` with real progress, the record survives a refresh,
and a deliberately broken job reaches `failed` with a safe error code. Re-running
a finished job leaves its verdict untouched.

What the exit criterion does **not** claim: the processing is a placeholder that
fingerprints the uploaded file. No video is analysed, no frame is decoded, and
no `Match` entity exists. Those are Phase 2 and Phase 3 work, and the interface
they will replace (`pickleball_worker.processors.VideoProcessor`) is deliberately
the only thing that has to change.

Carried into later phases:

- Real video metadata extraction in the worker
  (`pickleball_ml.video.reader.read_metadata`), replacing the placeholder.
- A `Match` entity, so calibrations, players and rallies have an owner. Until
  then the UI deliberately says "Videos", never "Matches".
- Serving uploaded video back to the browser for playback, which needs its own
  decisions about origin and content headers.
- No reaper for a job whose worker was killed, and no retry endpoint, so the
  `failed -> queued` transition is unreachable over HTTP.
- No authentication, so every upload is visible to anyone who can reach the
  port; the API binds to loopback for that reason.
- No browser-driven test. The opt-in suite drives the real components against
  the running stack in jsdom, which covers the data flow but not a real click.

---

## Phase 2 - Court Calibration

Start with manual calibration. Homography math and the CLI tool already exist from Phase 0; this phase brings calibration into the app.

Three prerequisites come first, because the calibration UI cannot be built
without them (see `docs/BACKEND.md`):

- the `Match` ownership model, so a calibration has something to belong to
- real video metadata extraction in the worker
  (`pickleball_ml.video.reader.read_metadata`), replacing the placeholder
- secure playback of the uploaded video, so a landmark can be clicked on a
  frame — served from an origin that is not the app's own

Build:

- web UI for clicking court landmarks
- calibration persistence in the database
- calibration quality display (reprojection error, court-line overlay)
- top-down court component

Exit criteria:

Clicking a player's location in the video produces the correct approximate location on the top-down court.

---

## Phase 3 - Player Tracking

Harden the Phase 0 prototype and move it into the worker.

Build:

- player detection + tracking as a worker stage
- filtering to four court players
- ID-switch recovery
- persistent player identity (team A/B) separate from per-frame court slot
- ground-contact position estimation
- court-coordinate player tracks (Parquet)
- animated top-down playback in the web app

Metrics:

- ID switches
- tracking coverage
- position error on manually sampled frames

Exit criteria:

All four players remain mostly correctly identified through multiple rallies.

---

## Phase 4 - Ball Tracking

Build/test:

- TrackNet-style baseline
- custom pickleball fine-tuning if needed
- temporal smoothing
- confidence values
- short-gap interpolation
- overlay visualization

Exit criteria:

Ball location is usable through enough of typical rallies to begin event detection.

Do not require perfect frame-by-frame tracking.

---

## Phase 5 - Rally Segmentation

Build:

- rally start/end heuristics
- rally timeline UI
- manual split/merge controls
- jump-to-rally video playback

Exit criteria:

Most rallies in a test match are correctly segmented or quickly correctable.

---

## Phase 6 - Basic Analytics

Implement reliable analytics that do not require shot classification:

- rally count
- rally duration distribution
- player movement distance
- movement speed
- court heatmaps
- time at/near kitchen
- baseline vs transition vs kitchen occupancy
- teammate separation
- kitchen arrival timing

Exit criteria:

Dashboard accurately reflects a manually reviewed test match.

This is the first strong portfolio milestone.

---

## Phase 7 - Human Correction System

Build correction tools for:

- rally boundaries
- player identity
- hit attribution
- shot labels

Store prediction + correction separately.

Exit criteria:

A user can fix model mistakes and analytics update accordingly.

---

## Phase 8 - Hit Detection

Build hit-event model/heuristic using:

- ball trajectory
- player proximity
- change in velocity/direction
- optional pose/paddle cues

Store confidence.

Exit criteria:

Hit timestamps and hitters are accurate enough on an annotated validation set for downstream shot analysis.

---

## Phase 9 - Shot Classification

Begin with deterministic rules.

Classes:

- serve
- return
- drive
- drop
- dink
- volley
- speed-up
- lob
- overhead
- unknown

Collect corrections and build a labeled dataset.

Only train a learned classifier if rules plateau.

---

## Phase 10 - Tactical Analytics

Examples:

- third-shot selection and outcomes
- kitchen transition after drive vs drop
- teammate spacing and rally outcomes
- transition-zone success
- direction/target tendencies
- repeated error locations
- shot placement patterns
- rally outcome conditioned on court position

Important:

Use sufficient sample-size thresholds. Do not generate tactical claims from two or three examples.

---

## Phase 11 - Evidence-Backed AI Coach

Feed structured metrics and supporting rally references to an LLM.

LLM responsibilities:

- rank notable patterns
- explain them clearly
- suggest practice/strategy adjustments
- cite the supporting stats/rallies

LLM must not invent game events.

Example output:

> Your third-shot drops led to successful kitchen entry in 9 of 12 attempts (75%), compared with 8 of 18 drives (44%). The difference appeared most often from the left service court. See rallies 3, 7, 11, and 18.

---

## Phase 12 - Automatic Court Detection

Once the rest works, replace or supplement manual calibration with a court keypoint detector.

Always preserve manual override.

---

# Recommended Semester/Portfolio Scope

If time is limited, stop after Phase 6 or 7.

A polished system that performs:

video upload -> court calibration -> player tracking -> top-down reconstruction -> rally segmentation -> analytics

is substantially stronger than an unfinished system that attempts every advanced feature.
