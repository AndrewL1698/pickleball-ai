/**
 * Test environment setup.
 *
 * `fetch` is stubbed per test rather than globally: the network is the only
 * boundary these tests mock, and each one should say what the server does.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
