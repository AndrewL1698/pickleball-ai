# Data Model

This document gives a conceptual schema. Exact implementation may evolve.

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
status
created_at
```

## VideoAsset

```text
id
match_id
storage_key
filename
width
height
fps
duration_seconds
frame_count
created_at
```

## ProcessingJob

```text
id
match_id
stage
status
progress
model_version
error_message
started_at
completed_at
```

## CourtCalibration

```text
id
match_id
source              # manual / model
image_points_json
court_points_json
homography_json
confidence
created_at
```

## Player

```text
id
match_id
team                 # near / far
position_slot        # optional initial slot
name                 # optional user-assigned name
is_user
```

## PlayerTrackPoint

```text
id
match_id
player_id
frame_number
timestamp_ms
image_x
image_y
court_x
court_y
confidence
processing_run_id
```

For scale, this table may eventually move to a more compact representation or columnar artifact rather than one SQL row per frame.

## BallTrackPoint

```text
id
match_id
frame_number
timestamp_ms
image_x
image_y
court_x
court_y
confidence
is_interpolated
processing_run_id
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
serving_team
winner_team
confidence
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
processing_run_id
```

Prefer deriving `resolved_shot_type` in application logic/view from prediction + corrections instead of duplicating it if possible.

## PredictionCorrection

Generic correction record:

```text
id
user_id
match_id
entity_type          # rally / shot / player_track / etc.
entity_id
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
started_at
completed_at
```

Prediction rows should reference a model run when practical.

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
