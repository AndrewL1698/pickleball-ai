/**
 * What each job status means, in words.
 *
 * Kept out of the components so the wording can be asserted in tests, and so
 * there is exactly one place where the honesty rules live.
 *
 * Two facts about this build shape all of the copy here:
 *
 * - Processing is a placeholder. It reads the uploaded file and records a
 *   checksum; no court, player, ball or rally analysis exists. A `ready` job
 *   must therefore never be described as an analysed match.
 * - `running` is often never observed. The placeholder finishes in
 *   milliseconds, so `queued -> ready` between two polls is normal, and the UI
 *   must not present `running` as a step that has to be seen.
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
    heading: "Checking the file",
    description: "A worker is reading the uploaded file.",
    announcement: "Job running.",
  },
  ready: {
    // Deliberately not "Analysis complete". The only thing that finished is a
    // file check, and saying otherwise would be a confidently wrong label.
    label: "Ready",
    heading: "File check complete",
    description:
      "The upload was read successfully and its checksum recorded. No match analysis has been performed.",
    announcement: "File check finished. No match analysis was performed.",
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
 * says what comes next and admits the build cannot do it yet, and nothing
 * here says "ready" or "analysed".
 */
export const MATCH_STATUS_COPY: Record<MatchStatus, { label: string; description: string }> = {
  uploaded: {
    label: "Uploaded",
    description: "The video is stored and waiting to be processed.",
  },
  processing: {
    label: "Processing",
    description: "A worker is processing the video.",
  },
  calibration_required: {
    label: "Needs calibration",
    description:
      "The file check finished. The court has to be calibrated next, and calibration is not available in this build yet.",
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
