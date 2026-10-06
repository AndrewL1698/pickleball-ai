/** Timestamp and duration rendering. */

import { describe, expect, it } from "vitest";
import {
  formatAverageFps,
  formatDuration,
  formatMediaDuration,
  formatTimestamp,
} from "@/lib/format";

describe("formatTimestamp", () => {
  it("renders an ISO timestamp as a readable local date", () => {
    const rendered = formatTimestamp("2026-09-23T18:00:52.792390Z");
    expect(rendered).not.toBe("Unknown");
    expect(rendered).toMatch(/2026/);
  });

  it("says Unknown rather than Invalid Date for junk", () => {
    expect(formatTimestamp("not a date")).toBe("Unknown");
    expect(formatTimestamp("")).toBe("Unknown");
  });
});

describe("formatDuration", () => {
  it.each([
    ["2026-09-23T18:00:00.000Z", "2026-09-23T18:00:00.250Z", "250 ms"],
    ["2026-09-23T18:00:00.000Z", "2026-09-23T18:00:02.500Z", "2.5 s"],
    ["2026-09-23T18:00:00.000Z", "2026-09-23T18:02:30.000Z", "2 min 30 s"],
  ])("renders %s to %s as %s", (start, end, expected) => {
    expect(formatDuration(start, end)).toBe(expected);
  });

  it("has nothing to say until the job has both ends", () => {
    expect(formatDuration(null, "2026-09-23T18:00:00Z")).toBeNull();
    expect(formatDuration("2026-09-23T18:00:00Z", null)).toBeNull();
    expect(formatDuration(null, null)).toBeNull();
  });

  it("refuses a negative duration rather than rendering nonsense", () => {
    expect(formatDuration("2026-09-23T18:00:05Z", "2026-09-23T18:00:00Z")).toBeNull();
  });
});

describe("formatMediaDuration", () => {
  it.each([
    [0, "0.0 s"],
    [12.46, "12.5 s"],
    [59.94, "59.9 s"],
    [60, "1:00"],
    [754.2, "12:34"],
    [3599.6, "1:00:00"],
    [3725, "1:02:05"],
  ])("renders %s seconds as %s", (seconds, expected) => {
    expect(formatMediaDuration(seconds)).toBe(expected);
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, -1])("says Unknown for %s", (seconds) => {
    expect(formatMediaDuration(seconds)).toBe("Unknown");
  });
});

describe("formatAverageFps", () => {
  it("labels the rate as an average, to two decimals", () => {
    expect(formatAverageFps(29.97002997)).toBe("29.97 fps average");
    expect(formatAverageFps(60)).toBe("60.00 fps average");
  });

  it.each([0, -5, Number.NaN])("says Unknown for %s rather than a rate", (fps) => {
    expect(formatAverageFps(fps)).toBe("Unknown");
  });
});
