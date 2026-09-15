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
Manual landmark selection + homography. Start with a CLI click tool on a single frame; a web calibration UI comes later.

### Later
Train/detect court keypoints automatically.

Landmarks (full list with court coordinates in `ARCHITECTURE.md`):

- four outer corners
- kitchen-line / sideline intersections
- center-line endpoints (where the center line meets the kitchen line and the baseline)

Do not use the net or net posts: they are elevated above the court plane and would bias the homography.

Validate every calibration by projecting court lines back onto the frame and recording reprojection error in feet.

Use least squares over all clicked points for manual calibration. Use RANSAC where appropriate when automatic predictions contain outliers.

## 2. Player Detection

Begin with a pretrained person detector.

Possible baseline:

- YOLO-family detector

Because the scene contains spectators or adjacent courts, filter detections using court geometry.

Useful rules:

- projected ground point must be near playing court (allow a margin behind the baselines, where players often stand)
- select four persistent tracks
- use court-side constraints within a game (teams switch ends between games)

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

- near/far court side (valid within a game only)
- appearance embedding
- previous trajectory
- jersey color
- spatial continuity

Identity is not court slot. Left/right position within a team changes during play (serve rotation, stacking), so derive near-left, near-right, far-left, and far-right per frame instead of treating them as player IDs.

Current implementation (Phase 0):

1. Gate detections by court position before tracking. Otherwise ByteTrack's IoU matching can move a far player's ID onto a person on a neighboring court whose box overlaps in the image.
2. Split tracks where the ground position moves faster than a person can, the box size jumps, or the torso color changes abruptly (the tracker handed the ID to an adjacent partner).
3. Select which fragments are players with a min-cost network flow per court half (at most two at a time; covered frames rewarded, implausible links penalized).
4. Assign fragments to partners with Viterbi over time, using torso color profiles per partner, motion continuity, and a penalty for switching a continuous track to the other partner.

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

Convert ground-plane positions into court coordinates (feet, origin at net center; see `ARCHITECTURE.md`).

- Players: project the ground-contact point (bottom center of the bounding box).
- Ball: the homography is only valid when the ball touches the court. Project bounce locations. Do not treat projected airborne positions as court locations; that needs a 3D trajectory estimate.

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
