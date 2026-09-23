import { defineConfig } from "vitest/config";

import { shared } from "./vitest.shared.mjs";

/** The opt-in suite that talks to a running API; see tests/live. */
export default defineConfig({
  ...shared,
  test: {
    ...shared.test,
    include: ["tests/live/**/*.live.test.{ts,tsx}"],
    testTimeout: 60000,
    // 01-upload must create the video that 02-status reads.
    fileParallelism: false,
    sequence: { shuffle: false },
  },
});
