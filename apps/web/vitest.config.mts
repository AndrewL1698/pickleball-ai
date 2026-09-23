import { defineConfig } from "vitest/config";

import { shared } from "./vitest.shared.mjs";

export default defineConfig({
  ...shared,
  test: {
    ...shared.test,
    include: ["tests/**/*.test.{ts,tsx}"],
    // tests/live needs a running API and worker; it has its own config.
    exclude: ["tests/live/**"],
  },
});
