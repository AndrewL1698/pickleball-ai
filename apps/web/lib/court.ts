/**
 * The court, for drawing and for naming landmarks.
 *
 * Court coordinates are feet with the origin under the centre of the net;
 * negative Y is the near half, nearer the camera. Mirrors
 * ml/src/pickleball_ml/court/spec.py. `npm run check:contract` checks the
 * landmark names against the API, and the API returns each landmark's position,
 * so a saved calibration is never drawn from this copy.
 */

import type { LandmarkName } from "./types";

export const HALF_WIDTH_FT = 10;
export const HALF_LENGTH_FT = 22;
export const KITCHEN_DEPTH_FT = 7;

const W = HALF_WIDTH_FT;
const H = HALF_LENGTH_FT;
const K = KITCHEN_DEPTH_FT;

/** In the order the API and the CLI ask for them. */
export const LANDMARKS: Record<LandmarkName, { label: string; x: number; y: number }> = {
  near_left_baseline_corner: { label: "Near baseline, left corner", x: -W, y: -H },
  near_center_baseline: { label: "Near baseline, center", x: 0, y: -H },
  near_right_baseline_corner: { label: "Near baseline, right corner", x: W, y: -H },
  near_left_kitchen: { label: "Near kitchen line, left end", x: -W, y: -K },
  near_center_kitchen: { label: "Near kitchen line, center", x: 0, y: -K },
  near_right_kitchen: { label: "Near kitchen line, right end", x: W, y: -K },
  far_left_kitchen: { label: "Far kitchen line, left end", x: -W, y: K },
  far_center_kitchen: { label: "Far kitchen line, center", x: 0, y: K },
  far_right_kitchen: { label: "Far kitchen line, right end", x: W, y: K },
  far_left_baseline_corner: { label: "Far baseline, left corner", x: -W, y: H },
  far_center_baseline: { label: "Far baseline, center", x: 0, y: H },
  far_right_baseline_corner: { label: "Far baseline, right corner", x: W, y: H },
};

type Segment = readonly [readonly [number, number], readonly [number, number]];

/** The painted lines. The net is drawn separately: it is not a landmark. */
export const PAINTED_LINES: readonly Segment[] = [
  [[-W, -H], [-W, H]],
  [[W, -H], [W, H]],
  [[-W, -H], [W, -H]],
  [[-W, H], [W, H]],
  [[-W, -K], [W, -K]],
  [[-W, K], [W, K]],
  [[0, -H], [0, -K]],
  [[0, K], [0, H]],
];

/** A court position in words. Says nothing about who is standing there. */
export function describeCourtPosition(x: number, y: number): string {
  const side = y < 0 ? "Near half" : "Far half";
  const inBounds = Math.abs(x) <= W && Math.abs(y) <= H;
  const behindKitchen = Math.abs(y) - K;
  const depth =
    behindKitchen < 0
      ? "inside the kitchen"
      : `${behindKitchen.toFixed(1)} ft behind the kitchen line`;
  return `${side}, ${depth}${inBounds ? "" : ", outside the court lines"}.`;
}
