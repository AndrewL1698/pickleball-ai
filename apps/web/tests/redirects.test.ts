/**
 * The Phase 1 `/videos` paths are deliberate redirects, not a second copy of
 * the pages. Each one must land on a `/matches` route that exists.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import nextConfig, { legacyRedirects } from "@/next.config";

const APP = join(__dirname, "..", "app");

describe("legacy /videos redirects", () => {
  it("are what next.config serves", async () => {
    expect(await nextConfig.redirects?.()).toEqual(legacyRedirects);
  });

  it("send the list and each video to their match equivalents", () => {
    expect(legacyRedirects).toEqual([
      { source: "/videos", destination: "/matches", permanent: false },
      { source: "/videos/:matchId", destination: "/matches/:matchId", permanent: false },
    ]);
  });

  it("are temporary, so no browser caches them for good", () => {
    expect(legacyRedirects.every((rule) => rule.permanent === false)).toBe(true);
  });

  it("point at routes that exist, and the old routes are gone", () => {
    expect(existsSync(join(APP, "matches", "page.tsx"))).toBe(true);
    expect(existsSync(join(APP, "matches", "[matchId]", "page.tsx"))).toBe(true);
    expect(existsSync(join(APP, "videos"))).toBe(false);
  });
});
