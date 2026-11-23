# Image coordinates are the source of truth for player tracks

Player tracking runs in two stages. Detect (person detection, court gate, ByteTrack) is slow: about an hour on a long match. Identify (player selection, partner assignment, changeover linking, projection into court coordinates) takes seconds. Detect stores image-space ground-contact points. Court coordinates are always derived by Identify from the current calibration. A recalibration or a correction (changeovers, identities) therefore re-runs Identify only, and never Detect.

## Consequences

The court gate that kept off-court people away from ByteTrack used the calibration that existed when Detect ran. The run records which calibration that was. If a recalibration moved the gate a lot, people could be wrongly gated in or out. The fix in that case is to re-run Detect on purpose, which recalibration never does by itself.
