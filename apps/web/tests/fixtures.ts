/**
 * Sample payloads, copied from real responses of the running API.
 *
 * Keeping them shaped exactly like the server's output is the point: a test
 * built on an invented payload proves nothing about the real contract.
 */

import { type ReactNode, createElement } from "react";
import type {
  CourtCalibration,
  Job,
  MatchDetail,
  MatchSummary,
  ReferenceFrame,
  Video,
  VideoMetadata,
} from "@/lib/types";

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
  metadata: null,
  camera_jumps: null,
};

/** A portrait phone clip: stored sideways, tagged 90 degrees, plays 1080x1920. */
export const VIDEO_METADATA: VideoMetadata = {
  width: 1080,
  height: 1920,
  rotation_degrees: 90,
  average_fps: 29.97,
  duration_seconds: 754.2,
  frame_count: 22603,
  codec: "hvc1",
  extracted_at: "2026-09-25T18:40:01.120000Z",
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
  can_extract_metadata: false,
  can_extract_reference_frames: false,
  can_calibrate: false,
  reference_frames: [],
  calibration: null,
};

export const REFERENCE_FRAME: ReferenceFrame = {
  id: "9a8b7c6d-1111-4222-8333-444455556666",
  kind: "background",
  position_seconds: null,
  source_frame_count: 25,
  width: 1920,
  height: 1080,
  created_at: "2026-10-31T18:00:55.310000Z",
};

export const SAMPLE_FRAME: ReferenceFrame = {
  ...REFERENCE_FRAME,
  id: "9a8b7c6d-1111-4222-8333-444455557777",
  kind: "sample",
  position_seconds: 188.6,
  source_frame_count: 1,
};

/**
 * An affine homography, not a real camera: X = x - 960, Y = 500 - y, w = 1.
 * Simple enough that a test can work out by hand what any pixel should come to.
 */
export const COURT_CALIBRATION: CourtCalibration = {
  id: "aaaa1111-2222-4333-8444-555566667777",
  reference_frame_id: REFERENCE_FRAME.id,
  source: "manual",
  image_points: {
    near_left_kitchen: [660, 800],
    near_right_kitchen: [1260, 800],
    far_left_kitchen: [760, 400],
    far_right_kitchen: [1160, 400],
    near_left_baseline_corner: [400, 1000],
  },
  court_points: {
    near_left_kitchen: [-10, -7],
    near_right_kitchen: [10, -7],
    far_left_kitchen: [-10, 7],
    far_right_kitchen: [10, 7],
    near_left_baseline_corner: [-10, -22],
  },
  homography: [
    [1, 0, -960],
    [0, -1, 500],
    [0, 0, 1],
  ],
  reprojection_error_ft: {
    near_left_kitchen: 0.31,
    near_right_kitchen: 0.12,
    far_left_kitchen: 0.2,
    far_right_kitchen: 0.14,
    near_left_baseline_corner: 0.4,
  },
  mean_reprojection_error_ft: 0.234,
  max_reprojection_error_ft: 0.4,
  created_at: "2026-10-31T18:05:00.000000Z",
  projected_lines: {
    near_baseline: [[400, 1000], [1520, 1000]],
    net: [[700, 500], [1220, 500]],
  },
  far_baseline_ft_per_pixel: 0.47,
  warnings: [],
};

export const CALIBRATABLE_MATCH: MatchDetail = {
  ...MATCH_DETAIL,
  status: "calibration_required",
  video: { ...VIDEO, metadata: VIDEO_METADATA, camera_jumps: [] },
  latest_job: JOB_READY,
  jobs: [JOB_READY],
  can_calibrate: true,
  reference_frames: [REFERENCE_FRAME, SAMPLE_FRAME],
};

export const CALIBRATED_MATCH: MatchDetail = {
  ...CALIBRATABLE_MATCH,
  status: "court_ready",
  calibration: COURT_CALIBRATION,
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
