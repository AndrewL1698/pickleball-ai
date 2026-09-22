# Phase 0b Results: Broader Feasibility

What this phase asked: can the Phase 0a prototype, built and tuned on one corner-camera
clip, be turned into a repeatable multi-clip experiment, and does a public ball tracker
give us enough of the ball to work with?

Everything below was produced by `experiments/phase0b.json`; every number has a command
that reproduces it. Where a measurement is missing, it says so rather than guessing.

**Exit criterion** ("a short clip can produce visibly reasonable player tracks and at
least partial ball tracks"): see [Assessment](#assessment-against-the-exit-criterion).

## 1. Footage

Four fixed-camera doubles recordings, all 1080p, all behind a baseline. None were
recorded for this project, which is itself useful: they cover setups a user might
plausibly produce. Videos stay out of the repository.

| Clip | Length | fps | Role | What it is |
|---|---|---|---|---|
| `gold` | 78.6 min | 30 | tune | Senior mixed doubles 3.5, outdoor, bright sun, adjacent courts and bleachers in frame |
| `buzz` | 14.7 min | 59.94 | tune | 5.0 men's doubles, outdoor, centred elevated camera, courts on both sides |
| `riggs` | 15.4 min | 59.94 | tune | Men's doubles rec play, indoor, wide-angle camera offset to the right; **camera moved at 6:08** |
| `pro63` | 21.0 min | 60 | held out | 6.3 DUPR men's doubles, indoor, fastest play, spectators along the bottom edge |

The Phase 0a clip (`testclip`, corner-mounted) is kept for comparison.

### Windows

Windows are sampled, not whole matches: the point is to cover conditions, not to burn
GPU hours. `tune` windows are the ones configuration was chosen on; `test` windows and
everything in `pro63` were left alone until the configuration was fixed.

| Window | Clip | Range | Split | Why this window |
|---|---|---|---|---|
| `gold_early` | gold | 2:00-5:00 | tune | Early play, team A near |
| `gold_switch` | gold | 12:40-17:00 | tune | Straddles the game break at 13:40-15:50; the teams change ends |
| `gold_mid` | gold | 40:00-43:00 | test | Middle of the long recording |
| `gold_late` | gold | 72:00-75:00 | test | Late, low sun, long shadows |
| `buzz_a` | buzz | 1:00-4:00 | tune | Best image quality; ball tracking; teams change ends at ~2:50 |
| `buzz_b` | buzz | 9:00-12:00 | test | Same camera, held out |
| `riggs_a` | riggs | 1:00-4:00 | tune | Off-centre wide-angle camera, first position |
| `riggs_b` | riggs | 10:00-13:00 | test | Second camera position after the camera was moved |
| `pro63_hard` | pro63 | 12:00-15:00 | test | Held-out hard condition |

## 2. Reproducing this

```bash
# One clip's calibration and camera report, then every window end to end
uv run pbml experiment run experiments/phase0b.json

# A single window, or a 20-second smoke test of each window
uv run pbml experiment run experiments/phase0b.json --window buzz_a
uv run pbml experiment run experiments/phase0b.json --smoke 20

# Debug videos and the collected summary
uv run pbml experiment run experiments/phase0b.json --stage render --stage ball_render
uv run pbml experiment summary experiments/phase0b.json
```

Each window writes `data/processed/<clip>/<window>/window_run.json` with the exact frame
range, source metadata, calibration used, every stage's configuration, model names and
versions, runtime and status. Stages skip themselves when their inputs and configuration
are unchanged, so the run above resumes rather than restarts.

## 3. Cameras

Camera placement is recovered from the calibration: the focal length comes from the
homography, the pose from `solvePnP` against the painted landmarks. Drift is ORB feature
matching against the first frame of the segment. This is the comparison the phase asked
for, including the Phase 0a corner camera.

| Camera | Height | Behind baseline | Lateral offset | Yaw | H-FOV | Calib. error (mean/max) | Corners in frame | Far/near scale | Drift (median/p95) |
|---|---|---|---|---|---|---|---|---|---|
| `buzz` | 8.3 ft | 11.4 ft | +0.4 ft | -0.3 deg | 82 deg | 0.08 / 0.15 ft | 4 of 4 | 0.23 | 3.2 / 6.2 px |
| `pro63` | 8.1 ft | 18.4 ft | +3.4 ft | -7.4 deg | 67 deg | 0.07 / 0.12 ft | 4 of 4 | 0.32 | 0.5 / 1.0 px |
| `riggs` cam1 | 7.3 ft | 10.3 ft | +4.8 ft | -10.6 deg | 97 deg | 0.27 / 0.49 ft | 4 of 4 | 0.23 | 0.4 / 0.6 px |
| `riggs` cam2 | 7.2 ft | 9.8 ft | +4.6 ft | -5.1 deg | 99 deg | 0.35 / 0.81 ft | 4 of 4 | 0.22 | 0.6 / 0.9 px |
| `gold` | 5.7 ft | 9.3 ft | -0.1 ft | +2.7 deg | 94 deg | 0.22 / 0.53 ft | **3 of 4** | 0.18 | 3.2 / 11.8 px |
| `testclip` (0a) | 4.4 ft | 2.3 ft | **-16.2 ft** | +48 deg | 97 deg | 0.28 / 0.66 ft | 4 of 4 | 0.89 | 0.2 / 0.5 px |

"Far/near scale" is how wide the far baseline is compared with the near one: the lower
it is, the more the far half is squashed and the less precise far-court positions get.

What the table supports:

- **Calibration error tracks field of view.** The two narrow-lens cameras (67 and 82
  degrees) fit to 0.07-0.08 ft mean error. The three wide ones (94-99 degrees) fit to
  0.22-0.35 ft. Fitting a single radial distortion coefficient barely moved this
  (gold 0.335 -> 0.335 ft, riggs cam1 0.307 -> 0.298 ft), so the wide-angle footage is
  already lens-corrected and the residual is landmark noise, not barrel distortion.
- **Camera height decides far-court precision.** At `gold`'s 5.7 ft the whole far half
  spans about 38 px, so one pixel of error is roughly 0.6 ft of depth; `pro63` at 8.1 ft
  and 18.4 ft back gets the best far/near scale of the set (0.32).
- **Distance behind the baseline decides whether players stay in frame.** See the player
  results: `gold` is only 9.3 ft back with a 94-degree lens, and its near players
  regularly step out of the frame.
- **Outdoor cameras drift.** The two outdoor clips move 3.2 px (median) against their
  first frame, `gold` up to 11.8 px; the two indoor ones stay under 1 px. No clip had a
  sudden jump except `riggs`, which was physically moved.
- **A moved camera needs a second calibration.** `riggs` jumps 104 px at 6:08. The
  manifest models this as two camera segments and refuses any window that crosses the
  move.

### Recommended recording setup

Supported by the table above:

- **Behind the baseline, centred**, within about 5 ft of the centre line. `riggs` at
  4.6-4.8 ft off centre still calibrates and tracks; the Phase 0a corner camera at 16 ft
  off centre is the one that produces an oblique view where players occlude each other.
- **At least 8 ft high.** 8 ft gave a far/near scale of 0.23-0.32; 5.7 ft gave 0.18 and
  0.6 ft of depth per pixel at the far baseline.
- **At least 12 ft behind the baseline**, more if the lens is wide. `pro63` at 18.4 ft
  keeps all four players in frame far more often than `gold` at 9.3 ft.
- **Main lens, not ultra-wide.** 67-82 degrees calibrated three to four times better than
  94-99 degrees.
- **1080p at 30 or 60 fps** was enough for players; the ball benefits from 60.
- **Mount it rigidly and do not touch it.** Outdoor drift of 3-12 px is tolerable, a
  104 px bump is not: it invalidates the calibration from that moment on.

## 4. Player tracking

Labels: sampled frames where every visible court player is named, so coverage and misses
are measurable and not just identity agreement. Frames are 10-15 s apart, which is also
the resolution of the ID-switch count: a swap shorter than that is invisible to it.
Labels are in `data/annotations/<clip>/player_labels_<window>.json`, and were made from
exported frames showing numbered detection boxes with no predicted identities attached.
Frames where a visible player could not be identified with confidence are marked `skip`
rather than guessed, which is why some windows have fewer labeled frames than exported.

| Window | Split | Labeled frames | Visible labels | Coverage | Identity accuracy | Frames with 4 visible / all 4 right | Extra predictions | ID switches |
|---|---|---|---|---|---|---|---|---|
| `gold_early` | tune | 18 | 65 | 0.92 | 1.00 | 12 / 8 | 0 | 0 of 56 |
| `gold_switch` | tune | 6 | 22 | 0.73 | 1.00 | 4 / 1 | 0 | 0 of 12 |
| `buzz_a` | tune | 12 | 45 | 0.96 | **0.81** | 9 / 3 | 0 | **2 of 39** |
| `riggs_a` | tune | 9 | 34 | 0.97 | 1.00 | 7 / 6 | 0 | 0 of 30 |
| `gold_mid` | test | 10 | 32 | 0.72 | 1.00 | 4 / 2 | 2 | 0 of 19 |
| `gold_late` | test | 4 | 16 | 0.88 | 1.00 | 4 / 2 | 0 | 0 of 10 |
| `buzz_b` | test | 11 | 42 | 0.98 | 1.00 | 9 / 8 | 0 | 0 of 37 |

Pooled (counts summed, not averaged over windows):

| Split | Visible labels | Coverage | Identity accuracy when detected | ID switches |
|---|---|---|---|---|
| tune | 166 | 0.916 | 0.947 | 2 of 136 |
| **test (held out)** | 90 | **0.867** | **1.000** | **0 of 66** |

"Coverage" is the share of labeled visible players that a predicted player box matched at
IoU 0.5 or better. "Identity accuracy" is, of those matches, the share whose predicted
player ID maps to the right person under the single best one-to-one mapping for the
window. "Extra predictions" are predicted player boxes in a labeled frame matching no
labeled player.

### What the failures are

**The 19% identity error is one specific situation.** Every wrong label in the set comes
from `buzz_a`, and all eight are the same two partners swapped with each other after the
teams changed ends. In that match all four players wear white tops; the clothing
descriptor separates those two partners by 0.199 on a 0-1 scale, against 0.54-0.69 for
the `gold` teams. Across the changeover the linking cost for that pair came out 0.603
against 0.640 for the alternative, which is a coin flip, and it landed wrong. The other
team in the same switch was linked correctly because two of its tracker IDs survived the
changeover and the continuity term decided it. `gold_switch`, where the teams wear
distinct colours, links correctly with no switches.

The evidence-supported conclusion: clothing colour is not a usable identity signal when
both teams wear the same kit, and the end-switch link needs something else. Tracker
continuity through the changeover works when it exists, which is roughly half the time
here. A correction UI that asks the user to confirm identities once per changeover would
close this completely, which is the Phase 7 plan anyway.

**Coverage losses are mostly players outside the frame, not tracking failures.** `gold`
resolves four players in only 14-38% of *all* frames, which looks alarming until the
labels are read: in labeled frames only 12 of 18 (`gold_early`) actually contain four
visible players. Reviewed frames show the near players standing behind the camera's field
of view. That camera is 9.3 ft behind the baseline with a 94-degree lens. Raising the
detector resolution from 1280 to 1920 px changed nothing (four gated players in 62% of
frames either way), confirming it is framing and not detection.

**Far-court positions are noisy on the low camera.** `gold` player rows reach 34 ft in
court coordinates, 12 ft past the far baseline, because one pixel of far-court error is
about 0.6 ft of depth at that camera height.

**Two extra predictions** in `gold_mid`, both a person near the court who is not a player.

**The confidence flag now covers this failure.** It did not at first: `identity_confident`
came only from the Viterbi margin of the partner assignment *inside* one game segment, so
every row after the `buzz_a` changeover was marked confident, including the eight wrong
ones. Each player now also carries `switch_link_margin`, the gap between the chosen
cross-switch pairing and the alternative, and a player whose identity rests on a link
below `switch_margin` is not confident afterwards. On `buzz_a` the two swapped players
carry a margin of 0.037 and are flagged for the whole post-switch segment; the two
correctly linked players carry 1.416 and stay confident. 19% of that window's rows are
now marked low-confidence, and they are the wrong ones. The threshold (0.15) is
provisional: it rests on two observed switches, one at 0.04 and one at 0.26-0.42.

### End switches

Teams changing ends inside a processed window broke Phase 0a's identity assumption. The
window is now cut at manually supplied changeover times, each game segment is resolved
independently, and players are linked across the cut by clothing prototype plus shared
tracker IDs. `gold_switch` keeps all four identities and their team labels across the
break (1.00 identity accuracy, 0 switches). Automatic detection of the changeover was not
attempted: the colour evidence for "the teams swapped" versus "they did not" was
1.80 against 1.97 on `gold_switch`, an 8% margin, which is not enough to act on
unsupervised.

## 5. Ball tracking

### Choosing a baseline

No custom model was trained, and none should be until a pretrained one has been measured.
Candidates, with what was checked:

| Candidate | Sport | License | Weights | Verdict |
|---|---|---|---|---|
| **WASB** ([nttcom/WASB-SBDT](https://github.com/nttcom/WASB-SBDT), BMVC 2023) | tennis, badminton, soccer, volleyball, basketball | MIT, (c) NTT Communications | Published per sport (6 MB each) | **Chosen** |
| TrackNetV3 ([qaz812345](https://github.com/qaz812345/TrackNetV3)) | badminton | MIT | Google Drive | Viable second choice; badminton-only and a heavier two-model pipeline (tracking + inpainting) |
| TrackNet, unofficial PyTorch ([yastrebksv](https://github.com/yastrebksv/TrackNet)) | tennis | **none stated** | Google Drive | Rejected: no license means no grant of rights |
| TrackNet-Pickleball ([AndrewDettor](https://github.com/AndrewDettor/TrackNet-Pickleball)) | **pickleball** | **none stated** | Google Drive | Rejected despite being the only pickleball-specific model: no license, last touched 2023 |
| Roboflow Universe pickleball models | pickleball | varies, dataset-level | Hosted API | Rejected for now: not a local, versioned, reproducible artifact |
| YOLO COCO "sports ball" | general | AGPL-3.0 (ultralytics) | Already downloaded | Kept as a comparison detector behind the same interface |

WASB won on license clarity (MIT code, published weights, a paper describing exactly how
they were trained), on being a single small model (1.5M parameters, 6 MB), and on being a
strong baseline across five sports rather than a single-sport reimplementation. Its
architecture is a small HRNet taking three consecutive frames at 512x288 and predicting
one heatmap per frame.

**Provenance and licensing.** The `wasb_tennis_best.pth.tar` weights come from the
authors' own model zoo and were trained on the TrackNet tennis dataset; the repository is
MIT and the architecture file is vendored into `ml/src/pickleball_ml/ball/wasb_hrnet.py`
with its Microsoft and NTT attribution intact and a note listing the three changes made
(training helper and demo removed, attribute-style config access replaced with dict
access, imports tidied). Weights are not committed; the README gives the one-line
download. **There is no pickleball model in the zoo, so this is a cross-sport transfer**
and should be read as such.

### How it is wired in

- `detector.py` turns an explicit frame window into candidate positions in source pixels,
  keeping source frame numbers and timestamps. Both detectors sit behind one interface.
- 60 fps footage is subsampled to about 30 fps, the rate the models were trained at, so
  apparent ball motion between input frames matches.
- `court_gate.py` marks which candidates lie in our court's image region. Nothing is
  dropped from the raw artifact.
- `track.py` picks one candidate per frame or "missing" with a second-order Viterbi pass
  that compares each candidate with a constant-velocity prediction. Nothing is
  interpolated by default, and interpolated rows, when enabled, are flagged and are never
  counted as observations.
- Artifacts: `ball_raw.parquet` (every candidate, with a gate flag), `ball_track.parquet`
  (one row per processed frame, missing rows kept), `ball_track.run.json`, and a debug
  video with detections, confidence and a trail.

### The adjacent-court problem

The first run on `buzz` produced a track that jumped across the whole frame: the tennis
model finds every ball, and this venue has a court on each side. Player detections are
gated by projecting the feet onto the court plane, but a ball is airborne, so that
projection is meaningless. The gate is therefore an image polygon: the court quad plus a
margin, with a cap above the far edge for balls in the air. On `buzz_a` it rejects 43% of
all candidates (882 of 2052). Its known limitation is that a ball lofted high over the
*near* half can leave the gate sideways; those frames go untracked rather than wrong.

### Measurements

Labels: `data/annotations/buzz/ball_labels_buzz_a.json`. Every 60th processed frame,
about 2 s apart. Candidates came from an independent HSV colour search for small bright
yellow-green blobs inside the court region, with recurring background positions removed,
and each was confirmed by eye on a zoomed crop. `visible` means a confirmed ball
belonging to this court; `absent` means none was found among the eight candidates or on
review, which can mislabel a ball completely hidden behind a player. Balls on the
neighbouring courts are never labeled as ours.

`buzz_a` (tune), 30 labeled frames: 14 visible, 16 absent.

| Setting | Coverage | Recall @20px | Precision @20px | Reports on ball-free frames | Median error |
|---|---|---|---|---|---|
| as first run (`min_score` 0.05) | 0.79 | 0.50 | 0.30 | 0.75 | 6.4 px |
| **tuned (`min_score` 0.35)** | 0.64 | **0.50** | **0.44** | **0.44** | 6.3 px |
| plus `min_run_frames` 5 | 0.36 | 0.29 | 0.57 | 0.13 | 3.7 px |

The tuned row is the manifest default: at the same recall it nearly halves the false
reports. The third row is available (`min_run_frames` requires N consecutive visible
frames) and trades a lot of recall for precision; with only 14 visible labels that
trade-off cannot be chosen responsibly, so it is left off and documented.

**What this says about the baseline.** When the tennis model is right it is accurate: the
median error of a correct report is 6 px, comfortably inside a pickleball's ~25 px
diameter near the camera. But it is right about half the time on labeled visible frames,
and it reports something on 44% of frames where our ball is not visible. That is a
partial tracker, not a usable one, and it is exactly what a cross-sport transfer with no
pickleball training data should be expected to look like.

Runtime: about 33 frames/s on an M4 for detection plus post-processing, so a 3-minute
window of 60 fps footage costs roughly 3 minutes of ball inference at 30 fps effective.

## 6. Failure cases worth carrying forward

1. **Identical kit defeats the appearance descriptor.** `buzz`'s partners are 0.199 apart
   in clothing distance; `gold`'s are 0.54-0.69. Everything the identity stage does with
   colour degrades accordingly, and across an end switch it becomes a coin flip.
2. **Near players leave the frame.** On `gold`, the camera is 9.3 ft behind the baseline
   with a 94-degree lens, and players standing back for a serve or a lob are simply not
   in the picture. No tracker can fix this; the recording setup has to.
3. **Adjacent courts.** Both the player detector (already gated in Phase 0a) and the ball
   detector (gated here) pick up the neighbouring game. The ball gate rejects 43% of
   candidates on `buzz_a`.
4. **A ball in flight over the near half** can leave the ball gate sideways, because the
   gate's cap follows the far edge. Those frames are untracked rather than mistracked.
5. **The ball is hard to label on a low camera.** On `gold` the ball is a few pixels
   across; an independent colour search plus visual confirmation was not able to produce
   reliable labels there, so `gold` has no ball metrics. This is a limitation of the
   evaluation, not just of the tracker.
6. **Far-court depth noise on low cameras.** Player rows on `gold` reach 12 ft beyond the
   far baseline. Pose-based ground contact was considered as a fix and not tested, so
   nothing is claimed about it.
7. **Outdoor camera drift** of 3-12 px was measured but its effect on court coordinates
   was not isolated; at `buzz`'s scale 6 px is roughly 0.1 ft near the camera.

## 7. Recommendation for the next phase

- **Player tracking is good enough to build on.** Held-out coverage 0.87 and identity
  accuracy 1.00 with zero ID switches, on three windows of two matches, is a working
  base for the top-down reconstruction and movement analytics of Phases 3 and 6.
- **Ball tracking is not, yet.** Keep WASB as the interface-compatible baseline and plan
  Phase 4 around fine-tuning it on pickleball frames. The labeling workflow needed to do
  that now exists; the cheapest next step is many more ball labels on `buzz` and
  `pro63`, since those two cameras give the clearest ball.
- **Do the correction UI before more identity heuristics.** The one identity failure mode
  left is exactly the one a single user confirmation per changeover removes.
- **Record with the recommended setup** and re-measure: 8+ ft high, 12+ ft back, main
  lens, centred within 5 ft.

## 8. Assessment against the exit criterion

> "A short clip can produce visibly reasonable player tracks and at least partial ball
> tracks."

**Player tracks: met.** Across seven labeled windows of three matches, 87-92% of visible
players are matched by a predicted player box, and of those matches 95% (tuning) and
100% (held out) carry the right identity, with no ID switches at all on held-out
windows over 66 opportunities. The rendered top-down video for a
held-out window puts the near players on the near baseline and the far players where
they actually stand. The one systematic failure, partners swapped after an end switch
when both teams wear the same kit, is understood, measured, and has a known fix.

**Ball tracks: met, narrowly, and only as "partial".** On the labeled window the tracker
reports a position on 64% of frames where the ball is visible and is within 20 px on 50%
of them, with a median error of 6 px when it is right. It also reports something on 44%
of frames where our ball is not visible. That is a partial ball track: enough to see the
ball follow real rallies in the debug video, not enough to build rally segmentation on
without more work.

**Verdict: Phase 0b passes**, with the ball half of the criterion met at the low end of
"partial". The phase's real output is not a working ball tracker but a measured one, plus
the experiment harness, the labeling workflow and the camera evidence needed to improve
it deliberately in Phase 4.

### What would change the verdict

If the ball numbers had to support rally segmentation today, this would be a fail. They
do not: Phase 5 is three phases away, and Phase 3 (player tracking in the worker) and
Phase 6 (movement analytics) depend only on the player half, which is comfortably met.
