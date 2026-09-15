# Architecture

## High-Level System

```text
                    +------------------+
                    |   Next.js Web    |
                    +--------+---------+
                             |
                             | HTTPS / REST
                             v
                    +------------------+
                    |     FastAPI      |
                    +--+------+-----+--+
                       |      |     |
          read/write   |      |     |  enqueue job
          +------------+      |     +-------------+
          v                   v                   v
   +-------------+   +----------------+   +-------------+
   | PostgreSQL  |   | Object Storage |   | Redis Queue |
   +-------------+   |  Video/Output  |   +------+------+
          ^          +----------------+          |
          |                   ^                  | consume
          |                   |                  v
          |                   |         +------------------+
          +-------------------+---------+  GPU/ML Worker   |
            results/status    artifacts +------------------+
                                          Court -> Players ->
                                          Ball -> Events ->
                                          Analytics
```

The worker reads source video from object storage, writes large artifacts (tracks, overlays) back to it, and writes job status, events, and metrics to PostgreSQL.

## Frontend Responsibilities

The web app handles:

- authentication
- video upload
- match list
- processing progress
- video player
- rally timeline
- interactive court visualization
- charts and statistics
- correction interface
- insight/evidence viewer

The frontend should not perform authoritative analytics calculations.

## API Responsibilities

FastAPI handles:

- authentication/authorization integration
- signed upload URLs or uploads during local development
- match CRUD
- processing-job creation/status
- analytics retrieval
- correction submission
- triggering recomputation

Heavy CV processing must run in workers.

## Worker Pipeline

Each match progresses through versioned stages:

```text
INGESTED
 -> METADATA_READY
 -> COURT_READY
 -> PLAYERS_READY
 -> BALL_READY
 -> RALLIES_READY
 -> HITS_READY
 -> SHOTS_READY
 -> ANALYTICS_READY
```

Not every MVP must reach every stage.

Each stage should be independently rerunnable so a new ball model does not require repeating court calibration.

## Artifact Storage

Store large artifacts outside PostgreSQL:

- original video
- transcoded proxy video
- thumbnails
- model weights
- debug overlays
- processed clips
- dense per-frame player and ball tracks (Parquet)

Store references/metadata in PostgreSQL. Sparse, correctable entities (rallies, shots, corrections, metrics, insights) live in PostgreSQL rows.

## Structured Event Flow

Do not calculate strategy directly from detector output.

```text
Raw Detection
   ↓
Smoothed Track
   ↓
Game Event
   ↓
Rally / Shot
   ↓
Metric
   ↓
Insight
```

This makes debugging possible.

## Geometry

Court calibration provides transformation H such that:

```text
image pixel (x, y) -> court coordinate (X, Y)
```

Use a projective homography for court-plane positions.

### Court Coordinate System

All court positions are stored in feet in a fixed court frame:

- Origin (0, 0): center of the court, directly under the net.
- Y: along the court, baseline to baseline. Negative Y is the near half (the half closer to the camera).
- X: across the court, sideline to sideline. Positive X is to the right for someone standing on the near baseline facing the net. For a camera behind the near baseline this is also the camera's right; for an oblique or corner camera, use this definition rather than the image's left/right.
- Positions outside the lines are valid (players often stand behind the baseline, |Y| > 22).

| Line | Definition |
|---|---|
| Sidelines | X = ±10 |
| Baselines | Y = ±22 |
| Net | Y = 0 |
| Non-volley (kitchen) lines | Y = ±7 |
| Center lines | X = 0, for 7 ≤ \|Y\| ≤ 22 |

Useful identities:

- court side = sign(Y)
- distance behind own kitchen line = |Y| - 7 (negative means inside the kitchen)

### Calibration Landmarks

Use painted intersections on the ground plane. "Near/far" and "left/right" in landmark names follow the court axes above (near = negative Y, left = negative X). They describe court geometry, not players.

| Landmark | (X, Y) |
|---|---|
| near_left_baseline_corner | (-10, -22) |
| near_center_baseline | (0, -22) |
| near_right_baseline_corner | (10, -22) |
| near_left_kitchen | (-10, -7) |
| near_center_kitchen | (0, -7) |
| near_right_kitchen | (10, -7) |
| far_left_kitchen | (-10, 7) |
| far_center_kitchen | (0, 7) |
| far_right_kitchen | (10, 7) |
| far_left_baseline_corner | (-10, 22) |
| far_center_baseline | (0, 22) |
| far_right_baseline_corner | (10, 22) |

Rules:

- A homography needs at least 4 points, no 3 of them collinear. Prefer every visible landmark, spread across both halves, and fit with least squares.
- Do not use the net or net posts. The net is elevated above the court plane.
- Record the reprojection error (in feet) with every calibration and surface it so bad calibrations can be reviewed.
- The homography assumes negligible lens distortion. Record with the phone's main (1x) lens.

### Ground Plane Only

The homography is only valid for points on the court plane.

- Players: human body boxes extend vertically out of the court plane, so use an estimated ground-contact point, generally the midpoint between the feet / bottom center of the bounding box.
- Ball: an airborne ball projected through H appears farther from the camera than it really is. Always store image coordinates. Treat court coordinates as meaningful only at bounces, or once a 3D trajectory model exists.

## Video Strategy

Do not store every decoded frame as an image.

Instead:

- retain original video
- index events by frame number and timestamp
- decode required regions during processing
- optionally create a lower-resolution proxy for UI playback

## Reprocessing

Every prediction should reference:

- model name
- model version
- model run (`ModelRun`)
- parameters/config
- creation timestamp

This lets evaluation compare model revisions.

## Corrections

Corrections are overlays on predictions rather than destructive edits.

Example:

```text
model prediction: shot_type = drive
user correction: shot_type = drop
```

Analytics use the resolved value:

```text
resolved = correction if present else prediction
```

## Security / Privacy

Because uploaded match videos may contain identifiable people:

- uploads should be private by default
- use expiring signed URLs in production
- authorize every match/video request
- do not use private user footage for training without explicit consent
- provide video deletion support

## Deployment Phases

### CV Prototype (current)
- `pbml` command-line tool from the `ml/` package
- one test video in `data/raw/`
- stage outputs as files in `data/processed/<video_name>/`
- no database, queue, API, or web app
- runs natively on Apple Silicon using the PyTorch `mps` device (Docker on macOS cannot use the GPU)

### Local Prototype
- Next.js dev server
- FastAPI
- PostgreSQL
- local video directory
- local ML process

### Portfolio Deployment
- frontend hosting
- API container
- managed Postgres
- object storage
- Redis
- GPU worker on demand or dedicated GPU host

Avoid building complex cloud infrastructure until the local pipeline works.
