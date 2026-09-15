# CLAUDE.md

## Project Overview

Build a full-stack machine-learning application that analyzes pickleball match footage recorded from a fixed smartphone camera and produces match statistics, tactical analytics, visualizations, and actionable coaching insights.

The long-term goal is to reconstruct enough of each match from video that the application can answer questions such as:

- Where does each player spend time on the court?
- How quickly do players reach the kitchen?
- What kinds of shots are being used?
- Where are shots landing?
- Which patterns correlate with winning or losing rallies?
- What strategic habits or weaknesses repeatedly appear?
- Can the app show the exact rallies that support an insight?

This is primarily a learning/portfolio project. Favor a robust, understandable system over trying to match commercial products immediately.

## Core Product Principle

Do not attempt to build one giant model that maps raw video directly to strategy advice.

Use a layered pipeline:

Video -> Court Geometry -> Player Tracking -> Ball Tracking -> Events -> Structured Match Data -> Analytics -> Coaching Explanation

Computer vision should determine what physically happened. Deterministic/statistical analytics should determine patterns. An LLM may explain those structured findings in natural language, but it should not invent match events from raw video.

## MVP Scope

Assume:

- Doubles pickleball only.
- One fixed phone camera.
- Landscape orientation.
- Main (1x) lens. Ultra-wide (0.5x) lens distortion breaks the planar homography.
- Camera positioned behind one baseline or elevated behind the court.
- Entire court remains visible, including the near baseline corners.
- No camera movement during the match.
- Standard court dimensions.

Do NOT initially support:

- handheld footage
- broadcast footage
- multiple camera angles
- moving cameras
- singles
- arbitrary partial-court recordings
- real-time inference

## MVP Features

First complete pipeline should support:

1. Video upload.
2. Background processing job.
3. Court calibration.
4. Detection/tracking of four players.
5. Ball tracking.
6. Rally segmentation.
7. Top-down court reconstruction.
8. Match/rally timeline.
9. Basic analytics dashboard.
10. Manual correction UI for uncertain predictions.

Initial analytics:

- rally count
- rally duration
- shot count if hit detection is reliable
- player movement distance
- player heatmaps
- time near kitchen
- kitchen arrival timing
- teammate spacing
- player positioning by rally phase
- ball landing/trajectory visualization when reliable

Later analytics:

- serve/return statistics
- shot type classification
- third-shot selection
- shot placement
- winners/errors
- transition-zone behavior
- target selection
- player-specific weakness detection
- tactical pattern mining
- natural-language coaching summaries

## Recommended Tech Stack

### Frontend
- Next.js
- React
- TypeScript
- Tailwind CSS
- Recharts or Plotly for charts
- SVG/Canvas for interactive court visualization

### Backend
- Python 3.13, managed with uv
- FastAPI
- Pydantic
- SQLAlchemy or SQLModel

### Database
- PostgreSQL

### Storage
- S3-compatible object storage for uploaded and processed videos
- Local filesystem is acceptable during development

### Background Jobs
- Redis
- Celery, RQ, or Dramatiq

### Computer Vision / ML
- PyTorch
- Ultralytics YOLO where useful
- OpenCV
- ByteTrack / BoT-SORT for player tracking
- TrackNet-style model or specialized temporal detector for ball tracking

### Infrastructure
- Docker / Docker Compose
- GPU worker may run separately from API server
- Local development is on Apple Silicon: run ML/CV code natively (PyTorch `mps` device), not inside Docker, because Docker on macOS cannot access the GPU

## Repository Structure

Suggested monorepo:

```text
pickleball-ai/
├── apps/
│   ├── web/                 # Next.js frontend
│   └── api/                 # FastAPI backend
├── ml/                      # uv workspace member, package `pickleball_ml`
│   ├── src/pickleball_ml/
│   │   ├── video/
│   │   ├── court/
│   │   ├── players/
│   │   ├── ball/
│   │   ├── events/
│   │   ├── shots/
│   │   └── evaluation/
│   └── tests/
├── workers/
│   └── video_processor/
├── packages/
│   └── shared/
├── data/
│   ├── raw/                 # source videos (gitignored)
│   ├── annotations/         # small hand-authored JSON labels (tracked); anything else ignored
│   └── processed/           # per-video stage outputs (gitignored)
├── scripts/
├── docs/
├── pyproject.toml           # uv workspace root
├── docker-compose.yml
├── README.md
└── CLAUDE.md
```

Create directories when they are first needed rather than scaffolding empty ones.

Keep research notebooks separate from production inference code.

## Processing Pipeline

### 1. Video Ingestion

When a user uploads a video:

1. Store original video.
2. Create Match record.
3. Create ProcessingJob record.
4. Queue async processing.
5. Extract metadata: FPS, width, height, duration, frame count.

Never process a long uploaded match inside the HTTP request lifecycle.

### 2. Court Detection / Calibration

Goal: map image coordinates into normalized court coordinates.

Preferred early implementation:

- Let the user manually click known court corners/keypoints.
- Compute homography using OpenCV.
- Save calibration with the match.

Automatic court detection can come later.

Represent court positions in court coordinates, not only pixels.

Standard dimensions:

- Court width: 20 ft
- Court length: 44 ft
- Non-volley zone extends 7 ft from each side of net

Court coordinate system (full spec and calibration landmarks in docs/ARCHITECTURE.md):

- Units: feet.
- Origin: center of the court, under the net.
- Y: along the court, Y in [-22, 22], negative on the near half (closer to the camera).
- X: across the court, X in [-10, 10], positive to the right for someone on the near baseline facing the net.
- Kitchen lines at Y = ±7; distance behind the kitchen line = |Y| - 7.

Calibrate only with painted ground-plane landmarks. The net is elevated and must not be used as a homography point.

The homography is only valid for points on the ground plane. Player positions use the foot/ground-contact point. An airborne ball projected through the homography lands in the wrong place, so ball court coordinates are only meaningful at bounces unless a 3D trajectory model is added.

### 3. Player Detection and Tracking

Start with a pretrained person detector.

Track the four players through frames using ByteTrack or BoT-SORT.

Keep persistent player identity separate from court position:

- Player identity (player 1-4, team A/B, later a user-assigned name) persists for the whole match.
- Court slot (near-left, near-right, far-left, far-right) is derived per frame from court coordinates.

A slot is never an identity: doubles partners swap left/right during play (serve rotation, stacking), and teams switch ends between games.

Do not assume tracker IDs remain perfect. Add logic to recover from ID switches using motion continuity, previous position, appearance, and court side within a game.

### 4. Ball Tracking

This is expected to be one of the hardest components.

Because the ball is tiny, blurred, and occasionally occluded, temporal information is important.

Investigate:

- TrackNet-style temporal heatmap prediction
- YOLO detector trained on high-resolution crops
- hybrid detector + Kalman/trajectory filtering
- interpolation across short occlusions

Keep raw ball detections, confidence values, and smoothed positions separately.

Always store ball image coordinates. Store court coordinates only where they are physically meaningful (see Court Detection / Calibration).

### 5. Rally Segmentation

Begin with heuristics and gradually add learned detection.

Potential signals:

- ball becomes active after serve
- repeated player-ball interaction
- ball disappears/out-of-bounds
- long inactivity period
- player reset behavior

Store rally boundaries explicitly so users can manually adjust them.

### 6. Hit Detection

Estimate hits using temporal features:

- ball approaches player
- ball reaches minimum distance to player/paddle region
- ball velocity/direction changes
- pose/paddle motion supports contact

Each hit should have:

- timestamp/frame
- player ID
- ball coordinate
- player coordinate
- confidence
- shot type if available

### 7. Shot Classification

Do not prioritize this before tracking works.

Possible classes:

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

Start with rule-based classification from position, trajectory, bounce, speed, and shot number. Train a learned temporal classifier only after collecting enough labeled examples.

### 8. Analytics Engine

The analytics layer should consume structured events, not raw video.

Examples:

- percentage of rally spent at kitchen
- time from return to kitchen arrival
- teammate separation distance
- court coverage
- third-shot choice
- success rate conditioned on shot choice
- error location
- opponent targeting
- rally win rate by court position

Every strategic insight should ideally contain supporting evidence and relevant rally IDs.

Example:

```json
{
  "insight_type": "third_shot_pattern",
  "summary": "Third-shot drops led to more successful kitchen transitions than drives.",
  "metrics": {
    "drive_attempts": 18,
    "drive_transition_rate": 0.44,
    "drop_attempts": 12,
    "drop_transition_rate": 0.75
  },
  "supporting_rallies": [3, 7, 11, 18, 22]
}
```

## Human-in-the-Loop Requirement

Prediction correction is a core feature, not an afterthought.

Allow users to correct:

- court calibration
- rally boundaries
- player identity
- player who hit a shot
- ball position when practical
- shot type
- winner/error label

Corrections should immediately recompute affected analytics.

Store both:

- original model prediction
- corrected ground-truth label

This creates future training data and makes imperfect models usable.

## Confidence Handling

Never present low-confidence model output as absolute truth.

Store confidence with detections and events.

Use thresholds such as:

- high confidence -> show normally
- medium confidence -> show with correction affordance
- low confidence -> mark unknown or request review

Prefer "unknown" over a confidently wrong label.

## Data Model Concepts

Major entities:

- User
- Match
- VideoAsset
- ProcessingJob
- CourtCalibration
- Player
- PlayerTrackPoint
- BallTrackPoint
- Rally
- Shot
- PredictionCorrection
- MatchMetric
- PlayerMetric
- Insight
- InsightEvidence
- ModelRun

Dense per-frame tracks (PlayerTrackPoint, BallTrackPoint) are stored as Parquet artifacts rather than one SQL row per frame.

See docs/DATA_MODEL.md for details.

## ML Evaluation

Evaluate each stage independently.

Player detection:
- precision/recall
- mAP

Player tracking:
- IDF1
- HOTA
- ID switches

Ball detection:
- detection precision/recall
- pixel error
- court-coordinate error

Rally segmentation:
- boundary tolerance accuracy
- precision/recall/F1

Hit detection:
- precision/recall/F1 with temporal tolerance

Shot classification:
- confusion matrix
- macro F1
- per-class accuracy

Analytics:
- compare automatically computed stats to manually labeled games

Do not claim the overall system is accurate because one detector has high mAP.

## Data Strategy

Use existing public datasets and pretrained models wherever licensing permits.

Collect a small custom dataset using standardized recording conditions.

Create annotation tooling early.

Prioritize labels in this order:

1. court keypoints
2. ball centers
3. player IDs/tracks
4. rally boundaries
5. hit frames + hitter
6. shot types
7. higher-level strategy labels only if actually needed

Avoid labeling strategy directly when it can be calculated from lower-level events.

## Engineering Principles

1. Keep ML inference deterministic and versioned.
2. Store model version with every prediction batch.
3. Never overwrite raw predictions when users correct them.
4. Separate raw detections from derived events.
5. Separate derived events from analytics.
6. Make processing resumable by stage.
7. Cache expensive outputs.
8. Keep video frame references so every statistic can link back to evidence.
9. Build the simplest working version before replacing components with ML.
10. Add automated tests for geometry and analytics calculations.

## Development Order

The project is CV-first: prove the vision pipeline on one known video with command-line scripts before building the web/API/database stack (see docs/ROADMAP.md Phase 0).

Follow this order unless there is a strong reason not to:

1. Repository setup
2. Video metadata extraction
3. Manual court calibration (CLI tool)
4. Player detection/tracking
5. Top-down player visualization (rendered debug video)
6. Ball tracking prototype
7. Upload + storage
8. Match/job database models + background jobs
9. Web match viewer and calibration UI
10. Rally segmentation
11. Match timeline
12. Basic analytics
13. Correction interface
14. Hit detection
15. Shot classification
16. Tactical analytics
17. LLM coaching summaries
18. Automatic court detection
19. Optimization/deployment

## Definition of a Strong First Demo

A strong first demo does NOT need advanced coaching.

It should:

1. Upload a real phone-recorded pickleball match.
2. Process it asynchronously.
3. Detect/calibrate the court.
4. Track four players.
5. Show their movement on a top-down court.
6. Track enough of the ball to segment rallies.
7. Let the user click a rally and jump to that part of the video.
8. Show movement/positioning analytics.
9. Allow corrections.

That is already a substantial full-stack + computer-vision project.

## Things Claude Should Avoid

When assisting with this repository:

- Do not add unnecessary microservices.
- Do not introduce Kubernetes for the MVP.
- Do not use an LLM to infer events that should come from the CV/analytics pipeline.
- Do not train custom models when a pretrained baseline has not been evaluated first.
- Do not silently alter database schemas without migrations.
- Do not build advanced shot classification before player/court/ball tracking is testable.
- Do not hide uncertainty from the frontend.
- Do not place long-running video processing inside API request handlers.
- Do not couple experimental notebook code directly to production endpoints.

## Coding Expectations

- Python: type hints, clear modules, pytest.
- TypeScript: strict mode.
- API schemas should be explicit and versionable.
- Use environment variables for secrets.
- Never commit uploaded videos, model weights, credentials, or large datasets to Git.
- Small hand-authored JSON annotations under `data/annotations/` (calibration clicks, evaluation labels) are tracked; generated outputs belong in `data/processed/`.
- Add README instructions whenever developer setup changes.
- Prefer small, reviewable commits/features.

## Immediate Milestone

Build an end-to-end prototype for one known test video:

Video -> manual court calibration -> player tracks -> homography -> animated top-down court.

Shape of the prototype:

- The test clip lives in `data/raw/` (gitignored).
- Python CLI scripts only: no database, queue, API, or web app yet.
- Each stage writes its output to `data/processed/<video_name>/` and the next stage reads it, so stages are resumable:
  1. video metadata
  2. `calibration.json` + court-line overlay image
  3. raw person detections/tracks (Parquet, with model version and config)
  4. filtered court-coordinate player tracks (Parquet)
  5. side-by-side debug video: original frame with boxes + animated top-down court
- Geometry and filtering logic have pytest unit tests.

Do not begin strategic coaching until this works reliably.
