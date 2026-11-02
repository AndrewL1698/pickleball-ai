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
 * a finished job leaves a match `calibration_required`, because nothing about
 * it has been analysed and the court still has to be calibrated, and saving a
 * calibration makes it `court_ready`.
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
 * Pipeline stage a job has reached, from docs/ARCHITECTURE.md. Jobs only reach
 * `metadata_ready` today; the rest exist so the field does not change meaning
 * when the real stages land.
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

/** The camera moved somewhere between these two sampled moments. */
export interface CameraJump {
  from_seconds: number;
  to_seconds: number;
  /** How far the picture shifted, in the video's pixels. */
  shift_px: number;
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
  /** Empty means checked and no move found. Null means not checked. */
  camera_jumps: CameraJump[] | null;
}

/** How a calibration image was made. */
export const REFERENCE_FRAME_KINDS = ["background", "sample"] as const;
export type ReferenceFrameKind = (typeof REFERENCE_FRAME_KINDS)[number];

/** A still from the video, to place calibration landmarks on. */
export interface ReferenceFrame {
  id: string;
  kind: ReferenceFrameKind;
  /** About where it came from, in seconds. Null for a background, from no single moment. */
  position_seconds: number | null;
  source_frame_count: number;
  /** The image's own pixels, which are the video's in display orientation. */
  width: number;
  height: number;
  created_at: string;
}

/** Who placed a calibration's landmarks. */
export const CALIBRATION_SOURCES = ["manual"] as const;
export type CalibrationSource = (typeof CALIBRATION_SOURCES)[number];

/** The painted intersections a calibration can use, in the order they are asked for. */
export const LANDMARK_NAMES = [
  "near_left_baseline_corner",
  "near_center_baseline",
  "near_right_baseline_corner",
  "near_left_kitchen",
  "near_center_kitchen",
  "near_right_kitchen",
  "far_left_kitchen",
  "far_center_kitchen",
  "far_right_kitchen",
  "far_left_baseline_corner",
  "far_center_baseline",
  "far_right_baseline_corner",
] as const;
export type LandmarkName = (typeof LANDMARK_NAMES)[number];

/** What `POST /api/matches/{id}/calibrations` takes. Points are in the frame's pixels. */
export interface CalibrationRequest {
  reference_frame_id: string;
  image_points: Partial<Record<LandmarkName, readonly [number, number]>>;
}

/**
 * A fitted calibration. `homography` maps image pixels to court feet, and only
 * means anything for a point on the ground. The error says how well the landmarks
 * agree with each other, not how accurate a position will be.
 */
export interface CourtCalibration {
  id: string;
  reference_frame_id: string;
  source: CalibrationSource;
  /** Pixels, keyed by landmark. Only the landmarks that were placed. */
  image_points: Record<string, [number, number]>;
  /** Feet, keyed by landmark. */
  court_points: Record<string, [number, number]>;
  homography: number[][];
  reprojection_error_ft: Record<string, number>;
  mean_reprojection_error_ft: number;
  max_reprojection_error_ft: number;
  created_at: string;
  /** Each court line's two ends in image pixels, for drawing an overlay. */
  projected_lines: Record<string, [[number, number], [number, number]]>;
  /** The most one image pixel can be worth at the far baseline, in feet. */
  far_baseline_ft_per_pixel: number | null;
  /** Fixed sentences for anything to review. Empty when there is nothing. */
  warnings: string[];
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
  /** Whether the API would accept a job for calibration images right now. */
  can_extract_reference_frames: boolean;
  /** Whether the API would accept a calibration right now. */
  can_calibrate: boolean;
  /** Stills to calibrate on, the background first. Empty until a job makes them. */
  reference_frames: ReferenceFrame[];
  /** The calibration in force, or null if the court has not been calibrated. */
  calibration: CourtCalibration | null;
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
