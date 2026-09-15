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

- collect 3-10 representative phone-recorded match clips
- standardize camera placement
- measure player tracking (ID switches, coverage) on sampled frames
- ~~link fragmented tracks into four persistent player identities~~ (first version done, see 0a revision); next: handle teams switching ends inside a window, reduce far-court position noise (e.g. pose keypoints for feet hidden by the net)
- record a clip with the recommended behind-baseline, elevated setup and compare against the corner-camera test clip
- test public/open-source pickleball or racket-sport ball trackers
- manually annotate a short evaluation clip
- decide initial ball-tracking baseline

Exit criteria:

A short clip can produce visibly reasonable player tracks and at least partial ball tracks.

---

## Phase 1 - Full-Stack Skeleton

Build:

- Next.js app
- FastAPI service
- PostgreSQL database
- Match page
- video upload
- background processing-job abstraction
- processing status UI

Exit criteria:

A user can upload a video and see a match record move through a mock/background processing job.

---

## Phase 2 - Court Calibration

Start with manual calibration. Homography math and the CLI tool already exist from Phase 0; this phase brings calibration into the app.

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
