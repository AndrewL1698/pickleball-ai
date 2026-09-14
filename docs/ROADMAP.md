# Project Roadmap

## Phase 0 - Research and Baselines

Goal: prove that the major CV components are feasible before building a large app.

Tasks:

- collect 3-10 representative phone-recorded match clips
- standardize camera placement
- test pretrained person detector
- test player tracker
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

Start with manual calibration.

Build:

- UI for clicking court landmarks
- homography computation
- calibration persistence
- image -> court coordinate transformation
- top-down court component

Exit criteria:

Clicking a player's location in the video produces the correct approximate location on the top-down court.

---

## Phase 3 - Player Tracking

Build:

- player detector
- multi-object tracker
- filtering to four court players
- near/far team assignment
- ground-contact position estimation
- court-coordinate player tracks
- animated top-down playback

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
