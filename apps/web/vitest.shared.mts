import react from "@vitejs/plugin-react";

/**
 * What both suites must agree on.
 *
 * Spread rather than merged: `mergeConfig` concatenates arrays, so the live
 * suite's `include` would be appended to the default one's and it would run
 * everything. Losing the shared `setupFiles` to a drift like that looks like
 * flaky cross-test pollution rather than a configuration mistake.
 */
export const shared = {
  plugins: [react()],
  // Resolves the "@/*" alias from tsconfig.json; Vite 8 does this natively.
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    css: false,
  },
};
