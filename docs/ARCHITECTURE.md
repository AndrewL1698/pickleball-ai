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
                    +---+----------+---+
                        |          |
                        |          +-------------------+
                        v                              v
                 +-------------+              +---------------+
                 | PostgreSQL  |              | Object Storage|
                 +-------------+              |  Video/Output |
                                              +---------------+
                        |
                        | enqueue
                        v
                 +-------------+
                 | Redis Queue |
                 +------+------+ 
                        |
                        v
                 +------------------+
                 | GPU/ML Worker    |
                 +------------------+
                   |   |   |   |
                   |   |   |   +--> Analytics
                   |   |   +------> Ball / Events
                   |   +----------> Players
                   +--------------> Court
```

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
- optional per-frame arrays

Store references/metadata in PostgreSQL.

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

Human body boxes extend vertically outside the court plane, so choose an estimated ground-contact point, generally the midpoint between the feet / bottom center of bounding box.

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
- processing run
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
