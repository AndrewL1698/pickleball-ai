/**
 * What each job status means, in words.
 *
 * Kept out of the components so the wording can be asserted in tests, and so
 * there is exactly one place where the honesty rules live.
 *
 * Two facts about this build shape all of the copy here:
 *
 * - Processing reads video metadata and makes calibration images, and nothing
 *   else. No player, ball or rally analysis exists, so a `ready` job must never
 *   be described as an analysed match.
 * - `running` can go unobserved. A short clip takes moments, so `queued -> ready`
 *   between two polls is normal, and the UI must not present `running` as a step
 *   that has to be seen.
 */

import type { JobStatus, MatchStatus } from "./types";

export interface StatusCopy {
  /** The word on the badge. Matches the API's own vocabulary. */
  label: string;
  /** The page heading for a job in this state. */
  heading: string;
  /** One sentence of explanation. */
  description: string;
  /** What a screen reader hears when the status changes. */
  announcement: string;
}

export const STATUS_COPY: Record<JobStatus, StatusCopy> = {
  queued: {
    label: "Queued",
    heading: "Waiting to start",
    description:
      "The upload is stored and the job is waiting for a worker to pick it up. If nothing happens, check that the worker process is running.",
    announcement: "Job queued, waiting for a worker.",
  },
  running: {
    label: "Running",
    heading: "Reading the video",
    description:
      "A worker is reading the video's resolution, duration and frame rate, and making calibration images.",
    announcement: "Job running.",
  },
  ready: {
    // Deliberately not "Analysis complete", which would be a confidently wrong
    // label. It also does not claim anything was saved: jobs from before
    // extraction existed finished too, and the sections below say what is known.
    label: "Ready",
    heading: "Processing finished",
    description:
      "This job finished. Processing covers video metadata and calibration images; no match analysis has been performed.",
    announcement: "Processing finished. No match analysis was performed.",
  },
  failed: {
    label: "Failed",
    heading: "Processing failed",
    description: "The job stopped before it finished.",
    announcement: "Job failed.",
  },
};

/** Statuses that will not change again without a new job. */
export function isFinished(status: JobStatus): boolean {
  return status === "ready" || status === "failed";
}

/**
 * What each match status means. The match status is the summary a person
 * sees first, so it carries the same honesty rules: `calibration_required`
 * says what comes next, `court_ready` says what still does not exist, and
 * nothing here says "ready" or "analysed".
 */
export const MATCH_STATUS_COPY: Record<MatchStatus, { label: string; description: string }> = {
  uploaded: {
    label: "Uploaded",
    description: "The video is stored and waiting to be processed.",
  },
  processing: {
    label: "Processing",
    description: "A worker is reading the video's metadata and making calibration images.",
  },
  calibration_required: {
    label: "Needs calibration",
    description: "The video has been processed. The court has to be calibrated next.",
  },
  court_ready: {
    label: "Court calibrated",
    description:
      "The court is calibrated. Player tracking, ball tracking and match statistics are not available yet.",
  },
  failed: {
    label: "Failed",
    description: "The latest processing attempt failed.",
  },
};
