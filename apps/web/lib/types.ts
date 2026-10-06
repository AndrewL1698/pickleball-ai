/**
 * The shapes the API actually returns.
 *
 * Hand-written rather than generated: the contract is a handful of shapes, and a
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

/** The rotations a video container can carry, in degrees. */
export type Rotation = 0 | 90 | 180 | 270;

/**
 * What the worker decoded from the video.
 *
 * `width` and `height` are in display orientation -- how the video plays --
 * after `rotation_degrees` was applied. `average_fps` is an average, not exact
 * frame timing: phone video is often variable frame rate, and
 * `duration_seconds` is frame count over that average, so an estimate too.
 */
export interface VideoMetadata {
  width: number;
  height: number;
  rotation_degrees: Rotation;
  average_fps: number;
  duration_seconds: number;
  frame_count: number;
  /** The stream's FourCC, or null when it does not name one. */
  codec: string | null;
  extracted_at: string;
}

/** The stored video behind a match. */
export interface Video {
  id: string;
  original_filename: string;
  content_type: string;
  byte_size: number;
  created_at: string;
  /** Null until a job has decoded it; never zero-filled. */
  metadata: VideoMetadata | null;
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
  /**
   * Whether the API would accept a metadata extraction request right now:
   * metadata is absent and no job is queued or running. The server decides;
   * the UI only offers the action when this is true.
   */
  can_extract_metadata: boolean;
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
