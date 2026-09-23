/**
 * The shapes the API actually returns.
 *
 * Hand-written rather than generated: the contract is two entities, and a
 * codegen step is more machinery than it earns here. The guard against drift
 * is `npm run check:contract`, which diffs these names against the live
 * OpenAPI schema (scripts/check-contract.mjs).
 *
 * Mirrors apps/api/src/pickleball_api/schemas.py.
 */

/** Lifecycle of one analysis attempt. `ready` and `failed` are terminal. */
export const JOB_STATUSES = ["queued", "running", "ready", "failed"] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

/**
 * Pipeline stage a job has reached, from docs/ARCHITECTURE.md. The placeholder
 * processor only ever reaches `metadata_ready`; the rest exist so the field
 * does not change meaning when the real stages land.
 */
export const JOB_STAGES = [
  "ingested",
  "metadata_ready",
  "court_ready",
  "players_ready",
  "ball_ready",
  "rallies_ready",
  "analytics_ready",
] as const;
export type JobStage = (typeof JOB_STAGES)[number];

/** Statuses the server will never move away from, so polling can stop. */
export const TERMINAL_STATUSES: readonly JobStatus[] = ["ready", "failed"];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/** An analysis job. Timestamps are ISO 8601 with a `Z` offset. */
export interface Job {
  id: string;
  video_id: string;
  status: JobStatus;
  stage: JobStage;
  /** 0.0 to 1.0. */
  progress: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** A video in a list. `latest_job` is null only if a job was never created. */
export interface VideoSummary {
  id: string;
  original_filename: string;
  content_type: string;
  byte_size: number;
  created_at: string;
  latest_job: Job | null;
}

/** One video and every attempt made at processing it. */
export interface VideoDetail extends VideoSummary {
  jobs: Job[];
}

export interface VideoList {
  videos: VideoSummary[];
  count: number;
}

/** Every error response has this shape; see main.py's exception handlers. */
export interface ApiErrorBody {
  error_code: string;
  detail: string;
}
