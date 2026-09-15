# Data Model

This document gives a conceptual schema. Exact implementation may evolve.

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
user_id
name
recorded_at
status               # overall lifecycle: uploaded / processing / ready / failed
created_at
```

`Match.status` is the user-facing summary. Per-stage pipeline progress lives in `ProcessingJob`.

## VideoAsset

```text
id
match_id
storage_key
filename
width
height
rotation_degrees     # phone videos often carry rotation metadata
codec
fps                  # average; phone video is often variable frame rate
duration_seconds
frame_count
created_at
```

Because phone video is often variable frame rate, derive timestamps from decoded frame timestamps, not `frame_number / fps`.

## ProcessingJob

```text
id
match_id
stage                # INGESTED / METADATA_READY / COURT_READY / ...
status               # queued / running / succeeded / failed
progress
model_run_id         # nullable; model details live in ModelRun
error_message
started_at
completed_at
```

## CourtCalibration

```text
id
match_id
source              # manual / model
frame_number        # frame the landmarks were placed on
image_points_json   # keyed by landmark name
court_points_json   # keyed by landmark name, feet
homography_json
reprojection_error_ft
confidence          # nullable for manual calibrations
created_at
```

## Player

```text
id
match_id
team                 # A / B, a persistent team identity
name                 # optional user-assigned name
is_user
```

Do not store court side or slot (near-left, far-right, ...) on `Player`. Teams switch ends between games and partners swap left/right during play, so side and slot are derived per frame from court coordinates.

## PlayerTrackPoint

Stored as a Parquet artifact per model run (one row per player per frame), not as SQL rows. Four players at 30 fps for an hour is about 430k rows per match.

```text
match_id
model_run_id
frame_number
timestamp_ms
track_id             # raw tracker ID
segment_id           # track split where position, box size, or clothing color jumps
player_id            # nullable until identity is resolved
identity_confident   # false when the alternative partner assignment was nearly as good
image_x              # ground-contact point
image_y
bbox_x1
bbox_y1
bbox_x2
bbox_y2
court_x
court_y
confidence
```

Keep raw tracker output (`track_id`) and resolved identity (`player_id`) separate, so identity logic can be rerun without re-detecting.

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
stage
model_name
model_version
config_json
artifact_storage_key # nullable; Parquet/overlay outputs for this run
started_at
completed_at
```

Every prediction (SQL row or Parquet artifact) should reference a model run.

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
