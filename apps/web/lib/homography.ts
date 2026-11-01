/**
 * Projecting one pixel through a saved calibration, for a preview.
 *
 * The server fits the homography and owns anything recorded from it. This only
 * shows a person where a point they just clicked lands.
 */

export type Point = readonly [number, number];

/**
 * A pixel's position on the court plane, or null when it has none.
 *
 * The plane meets the image along the horizon, and beyond it the sum still
 * produces numbers, for a point in the sky. Those come out with `w` of the
 * opposite sign from a point on the court, so `onCourt`, a pixel known to be on
 * the ground, is what tells them apart.
 */
export function projectToCourt(homography: number[][], pixel: Point, onCourt: Point): Point | null {
  const projected = homogeneous(homography, pixel);
  const reference = homogeneous(homography, onCourt);
  const w = projected[2];
  if (Math.abs(w) < 1e-12 || Math.sign(w) !== Math.sign(reference[2])) return null;
  const point: Point = [projected[0] / w, projected[1] / w];
  return Number.isFinite(point[0]) && Number.isFinite(point[1]) ? point : null;
}

function homogeneous(h: number[][], [x, y]: Point): [number, number, number] {
  return [
    h[0][0] * x + h[0][1] * y + h[0][2],
    h[1][0] * x + h[1][1] * y + h[1][2],
    h[2][0] * x + h[2][1] * y + h[2][2],
  ];
}
