/**
 * The shapes the API actually returns.
 *
 * Hand-written rather than generated: the contract is three entities, and a
 * codegen step is more machinery than it earns here. The guard against drift
 * is `npm run check:contract`, which diffs these names against the live
 * OpenAPI schema (scripts/check-contract.mjs).
 *
 * Mirrors apps/api/src/pickleball_api/schemas.py.
 */

/**
 * Where a match is in its lifecycle. There is deliberately no generic "ready":
 * a finished file check is `calibration_required`, because nothing about the
 * match has been analysed and the court still has to be calibrated.
 * `court_ready` is reserved; nothing sets it until calibration lands.
 */
export const MATCH_STATUSES = [
  "uploaded",
  "processing",
  "calibration_required",
  "court_ready",
  "failed",
] as const;
export type MatchStatus = (typeof MATCH_STATUSES)[number];

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

/** An analysis job. Timestamps are ISO 8601 with a `Z` offset. */
export interface Job {
  id: string;
  match_id: string;
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

/**
 * The stored video behind a match. Decoded metadata (duration, resolution,
 * frame rate) is not extracted yet, so it is not here.
 */
export interface Video {
  id: string;
  original_filename: string;
  content_type: string;
  byte_size: number;
  created_at: string;
}

/** A match in a list, with its video and the status of its most recent job. */
export interface MatchSummary {
  id: string;
  /** Defaults to the upload's filename without its extension. */
  name: string;
  /** When the game was played, if known. Not the upload time. */
  recorded_at: string | null;
  status: MatchStatus;
  created_at: string;
  /** Null only if the match has lost its video, which uploads never produce. */
  video: Video | null;
  /** Null only if a job was never created. */
  latest_job: Job | null;
}

/** One match and every attempt made at processing it, oldest first. */
export interface MatchDetail extends MatchSummary {
  jobs: Job[];
}

export interface MatchList {
  matches: MatchSummary[];
  /** The length of this page, not a total. */
  count: number;
}

/** Every error response has this shape; see main.py's exception handlers. */
export interface ApiErrorBody {
  error_code: string;
  detail: string;
}
