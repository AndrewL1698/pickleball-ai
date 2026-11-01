import { describe, expect, it } from "vitest";
import { LANDMARKS, describeCourtPosition } from "@/lib/court";
import { projectToCourt } from "@/lib/homography";
import { LANDMARK_NAMES } from "@/lib/types";

/** Every landmark's position, from the table in docs/ARCHITECTURE.md. */
const ARCHITECTURE: Record<string, [number, number]> = {
  near_left_baseline_corner: [-10, -22],
  near_center_baseline: [0, -22],
  near_right_baseline_corner: [10, -22],
  near_left_kitchen: [-10, -7],
  near_center_kitchen: [0, -7],
  near_right_kitchen: [10, -7],
  far_left_kitchen: [-10, 7],
  far_center_kitchen: [0, 7],
  far_right_kitchen: [10, 7],
  far_left_baseline_corner: [-10, 22],
  far_center_baseline: [0, 22],
  far_right_baseline_corner: [10, 22],
};

describe("landmarks", () => {
  it("has every landmark, at its documented position", () => {
    for (const name of LANDMARK_NAMES) {
      expect([LANDMARKS[name].x, LANDMARKS[name].y]).toEqual(ARCHITECTURE[name]);
    }
    expect(Object.keys(LANDMARKS).sort()).toEqual(Object.keys(ARCHITECTURE).sort());
  });
});

describe("describeCourtPosition", () => {
  it("names the half and the distance behind the kitchen line", () => {
    expect(describeCourtPosition(2, -12)).toBe("Near half, 5.0 ft behind the kitchen line.");
    expect(describeCourtPosition(2, 20)).toBe("Far half, 13.0 ft behind the kitchen line.");
  });

  it("says when a point is inside the kitchen", () => {
    expect(describeCourtPosition(0, -3)).toBe("Near half, inside the kitchen.");
  });

  it("says when a point is outside the lines", () => {
    expect(describeCourtPosition(11, 5)).toMatch(/outside the court lines/);
    expect(describeCourtPosition(0, -25)).toMatch(/outside the court lines/);
  });
});

describe("projectToCourt", () => {
  const IDENTITY = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  /** w = y / 100 - 1: a horizon at y = 100, with the ground below it (larger y). */
  const WITH_HORIZON = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0.01, -1],
  ];

  it("applies the homography, dividing by w", () => {
    expect(projectToCourt(IDENTITY, [3, 4], [0, 0])).toEqual([3, 4]);
    expect(projectToCourt(WITH_HORIZON, [50, 300], [0, 500])).toEqual([25, 150]);
  });

  it("refuses a point on the horizon", () => {
    expect(projectToCourt(WITH_HORIZON, [50, 100], [0, 500])).toBeNull();
  });

  it("refuses a point above the horizon, though the sum gives it numbers", () => {
    expect(projectToCourt(WITH_HORIZON, [50, 50], [0, 500])).toBeNull();
  });
});
