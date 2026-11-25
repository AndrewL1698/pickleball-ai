# Data Model

This document gives a conceptual schema. Exact implementation may evolve.

Implementation status (Phase 3): six of these tables exist.

| Here | In the database today |
|---|---|
| `Match` | `matches`, without `user_id` (there are no users yet) |
| `VideoAsset` | `videos`, with a unique `match_id` and nullable decoded metadata (Phase 2 checkpoint 2) |
| `ProcessingJob` | `analysis_jobs`, owned by `match_id`, plus an `error_code` column |
| `ReferenceFrame` | `reference_frames` |
| `CourtCalibration` | `court_calibrations`, without `confidence` until a model can produce one |
| `ModelRun` | `model_runs`, for the player-tracking stages `detect` and `identify` (Phase 3) |

Ownership points Match -> VideoAsset and Match -> ProcessingJob, so every later
table (`CourtCalibration`, `Player`, `Rally`, ...) has the same stable parent.
Deleting a match cascades to everything it owns, in the database
(`ON DELETE CASCADE`) as well as in the ORM. The status vocabularies below are
the implemented ones. See `BACKEND.md`.

Conventions:

- Court coordinates (`court_x`, `court_y`, `*_court_x`, `*_court_y`) are in feet, using the court coordinate system in `ARCHITECTURE.md` (origin at net center, negative Y on the camera side).
- Image coordinates are pixels in the source video's display orientation.
- `model_run_id` references `ModelRun`.

## User

```text
id
email
created_at
```

## Match

```text
id
user_id              # not yet: no User table exists
name                 # defaults to the upload's filename without its extension
recorded_at          # when the game was played, if known; not the upload time
status               # uploaded / processing / calibration_required / court_ready / failed
current_identify_run_id # nullable; the Identify ModelRun whose player tracks are shown
created_at
```

A match is one whole upload, so it can contain several games (`GLOSSARY.md`).

`Match.status` is the user-facing summary. Per-stage pipeline progress lives in `ProcessingJob`.

There is deliberately no generic `ready`: it would read as "the match has been
analysed". Each status names what has happened or what must happen next:

| Status | Meaning | Set by |
|---|---|---|
| `uploaded` | Stored; no processing attempt is running | Upload; a failed job requeued |
| `processing` | A job is running | The job moving to `running` |
| `calibration_required` | Processing finished; the court must be calibrated next | The job reaching `ready` |
| `court_ready` | A court calibration exists | Saving a calibration |
| `failed` | The latest attempt failed | The job moving to `failed` |

A job only ever changes the match's status through
`pickleball_api.jobs.transition`, so the two cannot disagree. A job finishing on
an already calibrated match leaves it `court_ready`.

## VideoAsset

```text
id
match_id               # unique: one video per match for the MVP
storage_key
filename               # `original_filename`: display metadata, never a path
width                  # display orientation, after rotation is applied
height                 # display orientation
rotation_degrees       # the container's rotation tag: 0 / 90 / 180 / 270
codec                  # FourCC; nullable even once decoded
average_fps            # an average: phone video is often variable frame rate
duration_seconds       # frame_count / average_fps, so an estimate
frame_count
metadata_extracted_at  # null until decoded; the decoded fields arrive together
camera_jumps           # JSON: where sampled frames show a move. Empty: none found. Null: not checked
created_at
```

Because phone video is often variable frame rate, derive timestamps from decoded frame timestamps, not `frame_number / fps`.

Implemented as nullable columns, because rows uploaded before extraction
existed were never decoded. They are null until a worker decodes the video and
are never zero-filled on failure. CHECK constraints require positive
dimensions and frame rate, a non-negative duration and frame count, a
right-angle rotation, and that the decoded fields are all present or all
absent (`codec` excepted). The worker writes them in the same transaction as
the job's `ready` transition.

`width` and `height` are in display orientation because that is what the
decoder produces: OpenCV applies the rotation tag and returns rotated frames, so
every image coordinate the pipeline records is a display-orientation pixel.

## ProcessingJob

```text
id
match_id
kind                 # preparation (metadata, calibration images) / players (player tracking)
stage                # INGESTED / METADATA_READY / COURT_READY / ...
status               # queued / running / ready / failed (ready = this job finished, not the match)
                     # at most one queued or running job per match, of any kind (partial unique index)
progress
model_run_id         # nullable; model details live in ModelRun
error_code           # stable, machine-readable reason for a failure
error_message        # a fixed sentence chosen by the code, never str(exception)
started_at
completed_at
```

`error_message` is never interpolated from an exception: a database error's text
contains the connection string, password included, and most others carry
absolute filesystem paths. The traceback is logged on the server instead.

A `players` job runs on a calibrated match and never changes `Match.status`: its
progress, failure and stage reached (`court_ready` while running, `players_ready`
when done) live on the job. A calibration may be saved while one runs.

## ReferenceFrame

A still to place landmarks on. Generated by the worker, in display orientation.

```text
id
match_id
ordinal              # display order, the background first; unique within a match
kind                 # background (median of frames, players removed) / sample
position_seconds     # approximate, from a seek; null for a background
source_frame_count   # decoded frames that went into it
storage_key
width
height
byte_size
created_at
```

## CourtCalibration

Rows are only added. A recalibration is a new row and the newest is in force.

```text
id
match_id
reference_frame_id        # the image the landmarks were placed on
source                    # manual; `model` arrives with automatic detection
image_points              # keyed by landmark name, pixels
court_points              # keyed by landmark name, feet
homography                # image pixels -> court feet
reprojection_error_ft     # keyed by landmark name
mean_reprojection_error_ft
max_reprojection_error_ft
created_at
```

The landmarks are placed on a still rather than a numbered frame, because a homography
belongs to the camera, and a background comes from no single frame.

## Player

```text
id
match_id
team                 # A / B, a persistent team identity; A is the near team when the match starts
number               # 1-4: 1-2 are team A, 3-4 team B; unique within a match
name                 # optional user-assigned name
is_user              # not yet: no User table exists
```

The first Identify run of a match creates all four; later runs reuse them. Within a
team, the lower number is the partner in their own left slot at the first frame both
are resolved (or the one seen first, if they never are), so re-running Identify keeps
the numbers.

Do not store court side or slot (near-left, far-right, ...) on `Player`. Teams switch ends between games and partners swap left/right during play, so side and slot are derived per frame from court coordinates.

## PlayerTrackPoint

Stored as the Parquet artifact of an Identify model run (one row per player per sampled frame), not as SQL rows. Four players at 15 samples a second for an hour is about 216k rows per match. `match_id` and `model_run_id` are not columns: every row of an artifact belongs to its run, and so to the run's match.

```text
frame_number
timestamp_ms
player_number        # 1-4; the match's Player is found by (match_id, number)
team                 # A / B, the player's team
side                 # near / far at this frame
slot                 # side + left / right from the player's own point of view (near_left, far_right, ...)
track_id             # raw tracker ID, unique within the Detect run (chunk * 1,000,000 + ByteTrack's id)
segment_id           # track split where position, box size, or clothing color jumps
tracklet_id          # segment cut where another segment on the same half starts or ends
x1                   # box, image pixels
y1
x2
y2
confidence           # detector score
image_x              # ground-contact point, image pixels, as Detect stored it
image_y
court_x              # ground-contact point projected with the run's calibration, feet
court_y
truncated            # box cut off by the bottom of the frame: the position is unreliable
identity_confident   # false when the partner assignment, or the link across a changeover, was nearly a coin flip
identity_margin      # cost gap of the partner assignment to the best alternative
switch_link_margin   # weakest changeover link this player was carried through (inf before the first)
```

Keep raw tracker output (`track_id`) and resolved identity (`player_number`) separate, so identity logic can be rerun without re-detecting. A frame where a player was not found has no row for them.

## BallTrackPoint

Stored as a Parquet artifact per model run.

```text
match_id
model_run_id
frame_number
timestamp_ms
image_x              # nullable when not detected
image_y
court_x              # nullable; only meaningful when the ball is on the ground (see ARCHITECTURE.md)
court_y
confidence
is_interpolated
```

## Rally

```text
id
match_id
rally_number
start_frame
end_frame
start_timestamp_ms
end_timestamp_ms
serving_team         # A / B
winner_team          # A / B
confidence
model_run_id
```

## Shot

```text
id
match_id
rally_id
shot_number
frame_number
timestamp_ms
hitter_player_id
predicted_shot_type
shot_type_confidence
resolved_shot_type
start_court_x
start_court_y
landing_court_x
landing_court_y
speed_mph
was_bounce_detected
model_run_id
```

Prefer deriving `resolved_shot_type` in application logic/view from prediction + corrections instead of duplicating it if possible.

## PredictionCorrection

Generic correction record:

```text
id
user_id
match_id
entity_type          # rally / shot / player_track / etc.
entity_id            # row ID; for Parquet tracks use a locator such as model_run_id + frame_number + track_id
field_name
original_value_json
corrected_value_json
created_at
```

## MatchMetric

```text
id
match_id
metric_name
value_json
analytics_version
created_at
```

## PlayerMetric

```text
id
match_id
player_id
metric_name
value_json
analytics_version
created_at
```

## Insight

```text
id
match_id
player_id            # nullable
insight_type
summary
metrics_json
confidence
analytics_version
created_at
```

## InsightEvidence

```text
id
insight_id
rally_id
shot_id              # nullable
start_timestamp_ms
end_timestamp_ms
importance
```

This enables the UI to show the exact footage supporting an insight.

## ModelRun

```text
id
match_id
stage                # detect / identify for now; later stages add members
model_version        # names the model as well as its version
config_json
config_hash          # SHA-256 of config_json (sorted keys) together with calibration_id
calibration_id       # the CourtCalibration the run used; required for both stages
artifact_storage_key # nullable until written; an Identify run's Parquet output (always null for Detect)
started_at
completed_at         # null while running, or if the run never finished; Detect sets it with its last chunk
parent_run_id        # nullable; the run this one read (Identify -> its Detect run)
```

Every prediction (SQL row or Parquet artifact) should reference a model run.

Player tracking is two runs (see
[ADR 0001](adr/0001-image-coordinates-are-the-source-of-truth-for-player-tracks.md)).
A `detect` run reads the video and writes image-space detections. An `identify` run
reads one `detect` run of the same match and writes court-coordinate player tracks.
A recalibration or a correction makes a new `identify` run and never a new `detect`
run. Both stages use a calibration: Detect gates people with it before ByteTrack,
and Identify projects into court feet with it. Because `calibration_id` is part of
`config_hash`, a run made under an old calibration never looks reusable after a
recalibration. Runs are never updated once complete and never deleted, so the original
prediction survives every correction, and a calibration a run used cannot be deleted
on its own. `Match.current_identify_run_id` says which
run the match is showing.

A Detect run takes about an hour over a whole match, so its output is a set of
chunks rather than one artifact, each committed as it finishes:

```text
ModelRunChunk
id
run_id               # the Detect run; deleting the run deletes its chunks
index                # 0, 1, ... from the start of the run's steady part; unique per run
start_seconds        # the stretch of video the chunk covers
end_seconds
storage_key          # one Parquet object per chunk
completed_at
```

A retry continues the run from its first unfinished chunk, with the run's own
calibration and configuration. A Detect run's `config_json` records the detector and
tracker settings, the sampling rate (about 15 per second, set in time), the chunk
length, and the steady part of the video it covers: its range, the parts excluded
because the camera moved (with when it moved), and whether the camera was checked at
all. Each detection row stores the image-space ground-contact point, `truncated`,
`in_gate`, an appearance descriptor, confidence and `track_id`, unique across chunks,
and no court coordinates.

## Key Design Rule

Keep these concepts distinct:

```text
MODEL PREDICTION
      +
USER CORRECTION
      ↓
RESOLVED EVENT
      ↓
ANALYTIC METRIC
      ↓
INSIGHT
```

This separation is essential for debugging, retraining, and trustworthy statistics.
