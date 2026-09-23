import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/** The opt-in suite that talks to a running API; see tests/live. */
export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/live/**/*.live.test.{ts,tsx}"],
    css: false,
    testTimeout: 60000,
    // 01-upload must create the video that 02-status reads.
    fileParallelism: false,
    sequence: { shuffle: false },
  },
});
