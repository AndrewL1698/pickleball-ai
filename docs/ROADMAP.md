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
  then the UI deliberately says "Videos", never "Matches". (Done in Phase 2
  checkpoint 1.)
- Serving uploaded video back to the browser for playback, which needs its own
  decisions about origin and content headers. (Moved to Phase 5 by Phase 2
  checkpoint 3.)
- No reaper for a job whose worker was killed, and no retry endpoint, so the
  `failed -> queued` transition is unreachable over HTTP.
- No authentication, so every upload is visible to anyone who can reach the
  port; the API binds to loopback for that reason.
- No browser-driven test. The opt-in suite drives the real components against
  the running stack in jsdom, which covers the data flow but not a real click.

---

## Phase 2 - Court Calibration

Start with manual calibration. Homography math and the CLI tool already exist from Phase 0; this phase brings calibration into the app.

Three prerequisites came first, because the calibration UI cannot be built
without them (see `docs/BACKEND.md`):

- the `Match` ownership model, so a calibration has something to belong to
- real video metadata extraction in the worker
  (`pickleball_ml.video.reader.read_metadata`), replacing the placeholder
- something to click on. This was written as secure playback of the uploaded
  video, and became generated still images instead (checkpoint 3)

Status (2026-09-24): checkpoint 1 done - the `Match` ownership model. A
`matches` table (name, `recorded_at`, status, `created_at`) owns one `Video`
(`videos.match_id`, unique) and every `AnalysisJob` (`analysis_jobs.match_id`,
replacing `video_id`). Migration `0003_match_ownership` carries Phase 1 rows
over, one match per video with the video's id, and has a tested downgrade on
SQLite and PostgreSQL. `Match.status` uses an explicit vocabulary —
`uploaded`, `processing`, `calibration_required`, `court_ready`, `failed` —
with no generic "ready"; a finished placeholder job leaves a match
`calibration_required`, and `court_ready` is reserved for the calibration
checkpoint. The API is match-oriented (`POST/GET /api/matches`,
`GET /api/matches/{id}`; `/api/videos` removed), and the web app moved to
`/matches` and `/matches/:id`, with `/videos` paths redirecting.

Status (2026-09-25): checkpoint 2 done - real video metadata extraction. The
worker's default processor is now `MetadataProcessor`, which reads width and
height (in display orientation), rotation, average frame rate, estimated
duration, frame count and codec with `pickleball_ml.video.reader.read_metadata`
and returns a typed result; the task layer saves it in the same transaction as
the job's `ready` transition. Migration `0004_video_metadata` adds nullable,
CHECK-constrained metadata columns and a partial unique index allowing one
active job per match, and moves backfilled `calibration_required` matches back to
`uploaded`, since none was ever decoded. `POST /api/matches/{id}/metadata-jobs`
retries a failed extraction or extracts metadata for an older match, and the
match page shows the metadata and offers that action only when it is usable.
The worker depends on `pickleball-ml` without its `tracking` extra, so its image
has OpenCV but not torch.

Status (2026-10-31): checkpoint 3 done - calibration images. Clicking a landmark
needs a still, not playback, so every job now also writes stills to storage: the
median of 25 frames across the video, which removes the players, and three single
frames for a video whose camera moved. They are decoded by the code the pipeline
uses, so a pixel on them is a pixel in every later image coordinate, and browser
support for a phone's codec does not come into it. Because they are JPEGs the
worker generated, the stored-XSS question about serving uploads does not arise.
Migration `0005_reference_frames`; a match processed before this queues the job
with `POST /api/matches/{id}/reference-frame-jobs`.

Video playback moves to Phase 5, where jump-to-rally is the first thing that needs
it, and takes its open questions about origin and headers with it.

Status (2026-10-31): checkpoint 4 done - calibration persistence and API.
Migration `0006_court_calibrations`. `POST /api/matches/{id}/calibrations` fits the
homography in the request and saves it. Calibrations are never updated: a new one
is a new row and the newest is in force. This changes the documented boundary: the
API now imports `pickleball_ml.court.geometry` and `court.spec`, so there is one
homography implementation, with a test that it still loads no model library. A
fit is refused when left and right are swapped, which no error measure can catch
because a mirrored court fits just as well. Tested against all six Phase 0
calibrations, each accepted as placed and refused when swapped.

Status (2026-10-31): checkpoint 5 done - the calibration page and top-down court.
**Phase 2 passes its exit criterion**, with the caveat below. `/matches/:id/calibrate`
places landmarks by click, drag or keyboard with a magnifier, shows the errors and
the projected lines, and puts a clicked point on the top-down court.

Verified in Chrome on the real stack, with the `buzz` clip, by replaying its Phase 0
landmarks through the page rather than a person placing them fresh:

| Check | Result |
|---|---|
| Fit through the page | 0.10 ft mean, 0.20 ft max, against 0.08 and 0.15 for Phase 0 |
| Projected lines against the painted lines | Sit on them, by eye |
| Near-left player's shoe, on the near baseline left of center | X -3.9, Y -22.0 |
| Near-right player's rear shoe, behind the baseline right of center | X 5.3, Y -22.8 |
| Far player's shoes, at the far kitchen line right of center | X 5.2, Y 8.0 |
| A point in the sky | Refused as above the horizon |
| Corner camera (`testclip`), through the API | 0.28 ft mean, 0.66 ft max, equal to Phase 0, overlay fits |

The caveat: the criterion says "in the video", and these clicks are on a still from
the video. Player positions above were judged against what the frame shows, not
measured. This fit differs from the Phase 0 one by 0.20 ft on average across the court and
0.67 ft at worst. The likely cause, not separately measured, is that a click resolves
to a screen pixel, about 2.1 image pixels at this width, and at the far baseline an
image pixel is about 0.25 ft. Arrow keys move a landmark by one image pixel, and the
page says so.

Status (2026-11-01): the first calibration placed by hand through the page, on the
full `riggs` video, with all 12 landmarks on the background image. It fit at 0.20 ft
mean and 0.52 ft max, with no warning. Against the two Phase 0 fits for that video,
over a 1 ft grid of the court:

| Phase 0 fit | Court difference, mean | Court difference, worst | Clicks apart |
|---|---|---|---|
| `cam2`, after the camera moved at 6:08 | 1.09 ft | 4.11 ft | 2-10 px (11 shared landmarks) |
| `cam1`, before the move | 4.01 ft | 10.09 ft | 66-172 px |

The fit follows `cam2`, which is consistent with about 60% of the video coming after
the move, so the background mostly shows that position. It is therefore about 4 ft out
for the first six minutes, while its error figures look good.

The gap to `cam2` is the far baseline, not the camera: the two images line up to 0.0 px,
the fits differ by under 0.5 ft on the near half and by up to 4.08 ft at the far baseline,
and the far-baseline clicks are 7-9 px apart, all in the same direction. Zoomed in, the
reason is visible. From this low camera the top of the net sits right beside the far
baseline and looks like a thick white line. The hand-placed clicks are on the thin
painted line beyond it, and the Phase 0 `cam2` clicks are on the near side of the net's
top, so that Phase 0 calibration is off by 3-4 ft at the far baseline. This was judged by
eye, supported by the hand-placed fit being the tighter one. The `riggs_b` results in
`docs/PHASE_0B_RESULTS.md` used that calibration, and what it cost them is unmeasured. The
annotation is unchanged, because the results were recorded against it. `cam1` was not
looked at in this way.

Status (2026-11-01): calibration fix-up, from those findings.

- A job now compares neighbouring sampled frames for a moved camera
  (`pickleball_ml.court.camera`), and the calibrate page warns before any landmark is
  placed. Migration `0007_camera_jumps`. Null means not checked, which is every video
  processed earlier, and there is no way to check one short of uploading it again. A
  move is a step between neighbouring samples over 20 px, or creep: the steps chained
  together since the camera last came to rest, over the same 20 px. Creep is chained
  rather than measured against the resting frame directly, because outdoors frames an
  hour apart match badly: on `gold` that direct comparison swings between 7 and 21 px
  while no step is over 7 px. "No move found" needs all 25 frames decoded and every pair
  compared, and an OpenCV failure in the check leaves the video unchecked instead of
  failing the job. At 25 frames per video:

  | Clip | Length | Largest step | Largest creep | Reported |
  |---|---|---|---|---|
  | `riggs` | 15.4 min | 107.4 px | 0.9 px | Moved between 5:50 and 6:27 (known: 6:08) |
  | `gold` | 78.6 min | 6.7 px | 15.9 px | Nothing |
  | `buzz` | 14.7 min | 4.0 px | 4.8 px | Nothing |
  | `pro63` | 21.0 min | 0.9 px | 1.3 px | Nothing |
  | `testclip` | 17.2 min | 0.4 px | 0.7 px | Nothing |

  `gold`'s outdoor creep comes within 4.1 px of the threshold, so a longer or windier
  outdoor match could be flagged as moved. Creep on `riggs` is measured apart from its
  move.

  It detects and warns, because moving cameras are out of MVP scope. With one moved clip
  the 20 px threshold is confirmed against four steady clips, not tuned on many moves, and
  a move and back between two samples would not show.
- A saved calibration reports the most one image pixel can be worth at the far baseline,
  worked out from the homography: 0.49 ft on the `riggs` hand-placed fit. It has no
  pass/fail threshold, because one video cannot justify one.
- The page now says to click the middle of the painted lines, warns about the net beside
  the far baseline, and will not save landmarks that have not changed.

Holding each landmark out of the fit and predicting it from the rest gives the first
measure of ground-plane error at known points:

| Calibration | Landmarks | Held-out mean | Held-out worst |
|---|---|---|---|
| `buzz` | 12 | 0.13 ft | 0.29 ft |
| `pro63` | 8 | 0.17 ft | 0.35 ft |
| `riggs`, hand-placed in the app | 12 | 0.32 ft | 0.74 ft |
| `gold` | 11 | 0.39 ft | 1.05 ft |
| `riggs` `cam1` | 12 | 0.42 ft | 0.87 ft |
| `testclip` | 11 | 0.44 ft | 0.92 ft |
| `riggs` `cam2` | 11 | 0.57 ft | 1.16 ft |

This only tests whether clicks agree with each other, so it missed the `cam2` far-baseline
error above, where a whole row of clicks was off together. These are one-off figures from
a script that was not kept.

Carried into later phases:

- One calibration cannot be right on both sides of a camera move. Phase 3 needs to decide
  what to do with a video that has one before applying a calibration to all of it.
- Placing 12 landmarks is tedious, and not practical on a phone. Proposed but neither
  decided nor built: tap the four corners, project the other landmarks, snap them to
  the painted lines, and confirm. Fully automatic detection stays in Phase 12.
- Reference frames and calibrations have no deletion path, like the rest of a match.
- The reprojection error measures how well clicks agree, not position accuracy, and
  the page says that too. Position accuracy for a player in court feet is still unmeasured.
- Sample positions are approximate because they come from seeking.

Build:

- ~~web UI for clicking court landmarks~~
- ~~calibration persistence in the database~~
- ~~calibration quality display (reprojection error, court-line overlay)~~
- ~~top-down court component~~

Exit criteria:

Clicking a player's location in the video produces the correct approximate location on the top-down court.

---

## Phase 3 - Player Tracking

Status (2026-11-22): in progress. Design settled. Built so far: the `ModelRun` table and run artifact storage (#7).

Harden the Phase 0 prototype and move it into the worker. Selection, ID-switch recovery,
partner assignment, linking across changeovers and the confidence margins already exist
in `pickleball_ml.players`, but only as CLI stages over short windows with changeover
times typed in by hand. Terms below follow `GLOSSARY.md`.

Build:

- player detection + tracking as a worker stage
- filtering to four court players
- ID-switch recovery
- persistent player identity (team A/B) separate from per-frame court slot
- ground-contact position estimation
- court-coordinate player tracks (Parquet)
- animated top-down playback in the web app

Design decisions (2026-11-22):

- **Trigger.** Saving a calibration queues a players job on `analysis_jobs`, and the job
  ends at `players_ready`. The match page shows its progress.
- **Two stages.** *Detect* (YOLO, court gate, ByteTrack) is slow and writes raw
  detections with image-space ground-contact points. *Identify* (selection, partner
  assignment, changeover linking, projection into court coordinates) takes seconds and
  writes the player tracks. Each run gets a `ModelRun` row (new table and migration) and a
  Parquet artifact stored through `Storage`. See
  [ADR 0001](adr/0001-image-coordinates-are-the-source-of-truth-for-player-tracks.md):
  recalibration, changeover edits and identity corrections re-run Identify only, as a
  small queued job, and never Detect.
- **Whole video, in checkpoints.** Detect covers the whole video, in 5-minute chunks
  processed in order and checkpointed, so a stop for any reason (crash, time limit, a
  future quota) loses only the current chunk and a retry resumes. Identify runs on
  whatever Detect has finished, so playback works for the processed part and the match
  says "Players tracked up to 34:10".
- **Detection rate is set in time**, about 15 detections per second, whatever the
  video's frame rate.
- **A camera that moved.** This answers the question carried over from Phase 2. Only the
  steady part of the video that contains the calibration's reference frame is tracked.
  The rest is reported as not processed ("the camera moved at 6:08"). Calibrating each
  steady part separately is out of MVP scope.
- **Ground-contact point** stays the bottom-centre of the box, and boxes cut off by the
  frame edge are marked unreliable. Position error is measured before any pose model is
  considered.
- **Team A** is the team on the near end when the match starts. Players are numbered 1-4
  by team, and within a team by slot when the players are first resolved. These numbers
  stay the same across Identify runs. Users rename players later.
- **Slot left/right** is taken from the player's own point of view (right slot = even
  court), not from the court axes. The court axes stay as they are for calibration
  landmarks. Today's code uses the court axes and has to change.
- **Changeovers** are proposed by Identify from every player crossing the net line within
  a short span. The match page lists them, with seek, confirm and reject buttons and "Add
  changeover here". Each edit re-runs Identify. An unconfirmed proposal is still used, but
  is shown as uncertain.
- **One identity correction in Phase 3**: "swap these partners". Every wrong identity in
  Phase 0b was two partners swapped after a changeover. A swap is stored in a new
  `PredictionCorrection` table against a time range (the period's start and end when the
  user made the correction), not against a period number. If a later changeover edit
  means the range no longer covers exactly one period, the swap is marked "needs review"
  and not silently applied. All other identity corrections wait for Phase 7.
- **Runs are kept.** Every Detect and Identify run keeps its row and artifact, and the
  match points at its current Identify run. The original prediction survives
  corrections. Pruning waits until storage matters.
- **Playback.** The metadata job also makes an H.264 MP4 playback copy (720p, or smaller
  if the source is smaller), streamed with range requests. The original stays untouched
  for the CV stages. A one-off CLI command makes copies for existing videos; until then,
  the page says "playback not available yet". The API serves player positions as
  column-wise JSON at about 10 Hz, and the top-down court follows the video's
  `currentTime`.
- **Uncertainty on the court.** A player whose identity is not confident is a hollow
  marker with "?". An unreliable ground-contact point is greyed out. A player unseen for
  more than about 1 s is removed rather than frozen in place.

Metrics:

- ID switches
- tracking coverage
- position error on manually sampled frames

Evaluation: full worker runs on the five Phase 0b videos, scored against the existing
`player_labels_*.json` windows, with `pro63_hard` held out, plus about 30 hand-clicked
ground-contact labels per video for position error. Results go in
`docs/PHASE_3_RESULTS.md`.

Exit criteria:

All four players remain mostly correctly identified through multiple rallies. On the
held-out data, that means:

- identity accuracy at least 0.85 before any user input
- identity accuracy at least 0.95 after changeovers are confirmed and partner swaps made
- every wrong identity row flagged as not confident

Coverage is reported but not required, because most of it is lost to players standing
outside the frame (Phase 0b).

Later: process and publish one **Game** at a time instead of fixed chunks, so if a long
upload hits a limit, the games before it are still finished. This waits until game
boundaries can be found from rallies (Phase 5 onward) or marked by the user.

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
- secure playback of the uploaded video, served so that an upload that is also
  valid HTML cannot run: origin, `Content-Disposition` and `X-Content-Type-Options`
  decisions, and a plan for codecs a browser cannot play
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

## Phase 13 - Social & Ratings

Status (2026-11-22): idea, after the MVP. Scope: a portfolio project for a real friend
group, not a public product. Terms follow `GLOSSARY.md`.

Turn the app into something like FotMob for pickleball. Every player gets a **match
rating** for each match, friends form **groups** with a leaderboard, and each user can
follow their ratings over time.

Depends on: Phase 6 (basic analytics), Phase 7 (corrections), and the four-corner
calibration proposed in Phase 2, because friends will calibrate on a phone. It does not
wait for Phase 12. The v1 rating also depends on Phase 10.

Build:

- user accounts. Which auth library or provider is open, decided when this phase starts
  and recorded as an ADR.
- groups: join by invite link; a user can be in several. A match is shared into one or
  more groups.
- tagging: the uploader links each of the match's four players to a user or marks them a
  guest. A tagged user must accept before the match shows on their profile or counts on a
  leaderboard. Guests count for nothing.
- game scores: the uploader types in each game's final score. They are stored as
  user-supplied truth, separate from anything the pipeline predicts. This gives every match
  a result and gives the **Game** boundaries that the "one game at a time" plan in Phase 3
  needs.
- match rating, 0-10 per player per match, always shown with the parts that add up to it
  ("+0.8 from third-shot drops"):
  - v0: a weighted mix of movement and positioning stats from Phase 6 (kitchen time,
    arrival speed, coverage, spacing), plus a small bonus for winning.
  - v1, after Phase 10: shots valued by how much they change the chance of winning the
    rally (like xG/xT in football). A player's rating is the sum of their shots.
  - Opponent strength is ignored, and the page says so.
- Player of the match: the player with the highest match rating in a published match.
  The number of these awards is the first long-term stat.
- profiles: a user's match ratings over time.
- group leaderboard: ranked by the average match rating over each player's last 10
  published matches, with at least 3 matches to appear. Side boards cover Player of the
  match awards and individual stats such as kitchen time.
- mobile-friendly web app installable to the home screen (PWA). A native app waits
  until the PWA demonstrably falls short.

Rules carried over from the core project:

- A match rating is published only once the events it uses are confident or corrected.
  Until then it is "provisional" and kept off leaderboards.
- Any tagged player can correct a shared match. Every correction is visible to the
  group, with who made it and the original prediction.
- Everything is visible only to the groups a match is shared into: the video, ratings and
  profiles. A profile shows only matches from groups the viewer shares with that user.
  Nothing is public.

Not now:

- a long-term skill rating (like DUPR). Friend groups don't play enough different
  opponents for it. Revisit after the Player of the match count.
- a public launch, a native app, hosted GPU processing.

---

# Recommended Semester/Portfolio Scope

If time is limited, stop after Phase 6 or 7.

A polished system that performs:

video upload -> court calibration -> player tracking -> top-down reconstruction -> rally segmentation -> analytics

is substantially stronger than an unfinished system that attempts every advanced feature.
