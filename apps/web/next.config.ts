import type { NextConfig } from "next";

/**
 * Phase 1 called the collection "Videos". It is "Matches" now, and the old
 * paths redirect rather than keeping a second copy of either page.
 *
 * `/videos/:id` can map straight onto `/matches/:id` because migration 0003
 * gave every Phase 1 video a match with the same id. A video uploaded since has
 * no `/videos/` link to follow, so nothing else needs translating.
 *
 * Temporary (307), not permanent: a 308 is cached by the browser for good, and
 * these are a courtesy for old bookmarks, not a contract.
 */
export const legacyRedirects = [
  { source: "/videos", destination: "/matches", permanent: false },
  { source: "/videos/:matchId", destination: "/matches/:matchId", permanent: false },
];

const nextConfig: NextConfig = {
  // Without this, Turbopack walks up looking for a lockfile and can settle on
  // one outside the repository, which changes what it resolves.
  turbopack: { root: __dirname },

  // A self-contained server bundle, so the Docker image does not need the
  // whole node_modules tree.
  output: "standalone",

  async redirects() {
    return legacyRedirects;
  },
};

export default nextConfig;
