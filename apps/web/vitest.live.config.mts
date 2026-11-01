import { defineConfig } from "vitest/config";
import { BaseSequencer, type TestSpecification } from "vitest/node";

import { shared } from "./vitest.shared.mjs";

/** By file name. The default order depends on file size and past results. */
class FileNameOrder extends BaseSequencer {
  async sort(files: TestSpecification[]): Promise<TestSpecification[]> {
    return [...files].sort((a, b) => a.moduleId.localeCompare(b.moduleId));
  }
}

/** The opt-in suite that talks to a running API; see tests/live. */
export default defineConfig({
  ...shared,
  test: {
    ...shared.test,
    include: ["tests/live/**/*.live.test.{ts,tsx}"],
    testTimeout: 60000,
    // 01-upload must create the video that 02-status reads.
    fileParallelism: false,
    sequence: { shuffle: false, sequencer: FileNameOrder },
  },
});
