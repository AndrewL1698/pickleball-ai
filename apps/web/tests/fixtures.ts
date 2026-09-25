/**
 * Sample payloads, copied from real responses of the running API.
 *
 * Keeping them shaped exactly like the server's output is the point: a test
 * built on an invented payload proves nothing about the real contract.
 */

import { type ReactNode, createElement } from "react";
import type { Job, MatchDetail, MatchSummary, Video } from "@/lib/types";

export const JOB_QUEUED: Job = {
  id: "4fc1f817-fe2d-4279-a0be-87125fd6c733",
  match_id: "7c0d8e0a-3b5e-4f7e-9a51-2f1f3c9d6b21",
  status: "queued",
  stage: "ingested",
  progress: 0,
  error_code: null,
  error_message: null,
  created_at: "2026-09-23T18:00:52.797614Z",
  started_at: null,
  finished_at: null,
};

export const JOB_RUNNING: Job = {
  ...JOB_QUEUED,
  status: "running",
  progress: 0.42,
  started_at: "2026-09-23T18:00:55.100000Z",
};

export const JOB_READY: Job = {
  ...JOB_QUEUED,
  status: "ready",
  stage: "metadata_ready",
  progress: 1,
  started_at: "2026-09-23T18:00:55.100000Z",
  finished_at: "2026-09-23T18:00:55.310000Z",
};

export const JOB_FAILED: Job = {
  ...JOB_QUEUED,
  status: "failed",
  progress: 0.5,
  error_code: "unreadable_video",
  error_message: "The video could not be read.",
  started_at: "2026-09-23T18:00:55.100000Z",
  finished_at: "2026-09-23T18:00:55.400000Z",
};

export const VIDEO: Video = {
  id: "1910ff1c-ff2f-4243-a343-5b7c59a86b12",
  original_filename: "demo.mp4",
  content_type: "video/mp4",
  byte_size: 2418,
  created_at: "2026-09-23T18:00:52.792390Z",
};

export const MATCH_SUMMARY: MatchSummary = {
  id: "7c0d8e0a-3b5e-4f7e-9a51-2f1f3c9d6b21",
  name: "demo",
  recorded_at: null,
  status: "uploaded",
  created_at: "2026-09-23T18:00:52.792390Z",
  video: VIDEO,
  latest_job: JOB_QUEUED,
};

export const MATCH_DETAIL: MatchDetail = {
  ...MATCH_SUMMARY,
  jobs: [JOB_QUEUED],
};

/** A `Response` the way the API sends one. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * The `next/link` stub every component test needs.
 *
 * Several tests assert the row really is an anchor, so the stub has to render
 * one. Exported rather than repeated, so a fifth test file cannot quietly
 * diverge; each file still calls `vi.mock("next/link", nextLinkMock)` itself,
 * because `vi.mock` is hoisted per file.
 */
export function nextLinkMock() {
  return {
    default: ({ children, href }: { children: ReactNode; href: string }) =>
      createElement("a", { href }, children),
  };
}

/** Wait, when a test needs real time to pass. */
export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A video file that passes client-side validation. */
export function videoFile(name = "match.mp4", size = 1024): File {
  const file = new File([new Uint8Array(Math.min(size, 1024))], name, { type: "video/mp4" });
  // File size is derived from its contents, so a large file is faked rather
  // than allocated: a 3 GB Uint8Array in jsdom is not a reasonable test.
  Object.defineProperty(file, "size", { value: size });
  return file;
}
