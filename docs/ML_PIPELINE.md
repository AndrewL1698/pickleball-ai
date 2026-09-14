# Machine Learning / Computer Vision Pipeline

## Guiding Philosophy

Treat match understanding as several measurable subproblems instead of one monolithic model.

```text
Frame sequence
 -> Court
 -> Players
 -> Ball
 -> Tracks
 -> Rallies
 -> Hits
 -> Shot types
 -> Analytics
```

Each stage should expose debug visualizations and evaluation metrics.

## 1. Court Calibration

### MVP
Manual landmark selection + homography.

### Later
Train/detect court keypoints automatically.

Potential landmarks:

- four outer corners
- kitchen-line intersections
- center-line endpoints
- net line intersections

Use RANSAC where appropriate when automatic predictions contain outliers.

## 2. Player Detection

Begin with a pretrained person detector.

Possible baseline:

- YOLO-family detector

Because the scene contains spectators or adjacent courts, filter detections using court geometry.

Useful rules:

- projected ground point must be near playing court
- select four persistent tracks
- maintain court-side constraints

## 3. Player Tracking

Candidate trackers:

- ByteTrack
- BoT-SORT
- DeepSORT as comparison baseline

Challenges:

- occlusion at net
- players crossing visually
- adjacent courts
- temporary missed detections
- ID switches

Additional identity signals:

- near/far court side
- appearance embedding
- previous trajectory
- jersey color
- spatial continuity

## 4. Ball Tracking

This is likely the hardest visual problem.

Problems:

- tiny object size
- motion blur
- high velocity
- intermittent occlusion
- compression artifacts
- visually similar white/yellow objects

### Baseline approaches

A. TrackNet-style temporal heatmap model

Input several consecutive frames and output likely ball location heatmap.

B. High-resolution object detector

Tile/crop court region and train a small-object detector.

C. Hybrid

Detector proposes candidate observations; motion model and temporal network reject implausible candidates.

### Post-processing

Use:

- confidence filtering
- maximum physically plausible velocity
- trajectory continuity
- Kalman filtering where helpful
- short-gap interpolation

Retain missing values when uncertainty is high rather than creating fabricated tracks.

## 5. Coordinate Transformation

Convert ball and player ground positions into court coordinates.

Benefits:

- camera-independent analytics
- easier shot placement visualization
- interpretable distances
- geometry-based rules
- standardized training features

## 6. Rally Segmentation

Possible features:

- ball visibility/motion state
- serve-like event
- sequence of hits
- long stationary gap
- players reset to service positions

A heuristic system is acceptable initially.

## 7. Hit Detection

For each candidate contact window, derive temporal features:

- distance between ball and candidate hitter
- incoming/outgoing velocity
- angle change
- acceleration
- hitter hand/paddle pose if available
- court side of hitter

Train a small temporal classifier only when enough labels exist.

Evaluation should tolerate a small timestamp/frame window around manual hit labels.

## 8. Hitter Attribution

Given a detected hit:

1. determine legal team side from trajectory
2. find plausible nearby players
3. compare ball-player distance
4. use motion/pose cues
5. apply previous hitter/team alternation constraints carefully

Return confidence.

## 9. Bounce Detection

Potential signals:

- trajectory kink near court plane
- vertical motion reversal in image/3D approximation
- local velocity change
- court-coordinate location

Bounce detection is useful for:

- serve validity
- return identification
- groundstroke vs volley
- shot landing placement

Do not block early MVP on perfect bounce detection.

## 10. Shot Classification

Start with engineered features:

- shot index within rally
- hitter court position
- prior bounce
- outgoing speed
- trajectory apex
- depth
- landing region
- kitchen proximity
- player pose/motion if available

Rule examples:

- first hit of rally from server -> serve
- second valid hit -> return
- slow shot from kitchen to opponent kitchen -> likely dink
- shot from baseline intended to land in kitchen -> likely drop
- high-speed baseline groundstroke -> likely drive

Rules produce weak labels that can help organize manual review, but do not assume they are ground truth.

## 11. Tactical Pattern Layer

This layer should not require deep learning initially.

Use SQL/Python analytics over structured events.

Examples:

### Third-shot selection

Group rallies by third shot:

- drive
- drop
- other

Compare:

- kitchen arrival
- fifth-shot quality
- rally win rate

### Team spacing

At each timestamp:

```text
distance = || teammate_A - teammate_B ||
```

Aggregate spacing immediately before errors or lost rallies.

### Transition-zone behavior

Define zones using court coordinates:

- baseline/backcourt
- transition zone
- non-volley line / kitchen

Compute shot outcomes conditioned on zone.

## 12. Evaluation Dataset

Create a fixed validation set early.

For several short clips manually label:

- court landmarks
- player IDs periodically
- ball center per frame for selected rally windows
- rally boundaries
- hits
- hitter
- shot type eventually

Never evaluate on the same clips repeatedly used for tuning without maintaining a held-out test set.

## 13. Data Versioning

Every annotation should include:

- source video ID
- frame/timestamp
- annotation type
- label
- annotator
- revision

Every model result should include:

- model version
- checkpoint hash/version
- inference config
- video version

## 14. Learning Strategy

Recommended order:

1. pretrained baseline
2. measure failure modes
3. collect targeted labels
4. fine-tune only where necessary
5. measure again

Do not create a custom neural network merely because the project is an ML project.

The learning value comes from building and evaluating the system intelligently.
