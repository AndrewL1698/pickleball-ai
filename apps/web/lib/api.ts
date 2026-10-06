/**
 * The only place that talks to the backend.
 *
 * Everything goes through `request`, so the four ways a call can go wrong --
 * the server is unreachable, it answered with a 4xx/5xx, it answered with
 * something that is not JSON, or the caller aborted -- all arrive at the UI as
 * one `ApiError` with a sentence worth showing a person.
 */

import type {
  ApiErrorBody,
  Job,
  MatchDetail,
  MatchList,
} from "./types";

/**
 * Read at module load. `NEXT_PUBLIC_` values are inlined into the browser
 * bundle at build time, so this is a build-time choice, not a runtime one --
 * see docs/BACKEND.md for what that means for the Docker image.
 */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** A failed call, already reduced to something a person can read. */
export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;

  constructor(message: string, options: { status?: number | null; code?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? null;
    this.code = options.code ?? "network_error";
  }

  /** True when retrying the same call might plausibly work. */
  get isRetryable(): boolean {
    return this.status === null || this.status >= 500;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ApiErrorBody).error_code === "string" &&
    typeof (value as ApiErrorBody).detail === "string"
  );
}

/** The sentence shown when the server said nothing useful. */
function messageForStatus(status: number): string {
  if (status === 404) return "That item no longer exists.";
  if (status >= 500) return "The server had a problem. Try again in a moment.";
  return "The request was rejected.";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch (cause) {
    // fetch only rejects for network-level failures, and an aborted request is
    // the caller's own doing rather than something to report.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(
      `Could not reach the API at ${API_BASE_URL}. Is it running?`,
      { code: "unreachable" },
    );
  }

  // 204 and friends have no body to parse.
  const text = await response.text();
  let body: unknown = null;
  if (text.length > 0) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    throw isApiErrorBody(body)
      ? new ApiError(body.detail, { status: response.status, code: body.error_code })
      : new ApiError(messageForStatus(response.status), {
          status: response.status,
          code: "unexpected_response",
        });
  }

  if (body === null) {
    throw new ApiError("The server sent a response that could not be read.", {
      status: response.status,
      code: "malformed_response",
    });
  }
  return body as T;
}

export function listMatches(signal?: AbortSignal): Promise<MatchList> {
  return request<MatchList>("/api/matches", { signal, cache: "no-store" });
}

export function getMatch(matchId: string, signal?: AbortSignal): Promise<MatchDetail> {
  return request<MatchDetail>(`/api/matches/${encodeURIComponent(matchId)}`, {
    signal,
    cache: "no-store",
  });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  return request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`, {
    signal,
    cache: "no-store",
  });
}

/**
 * Queue a metadata extraction job for a match whose metadata is absent --
 * after a failure, or for a match that predates extraction. The API answers
 * 409 when it already has metadata or a job is active, and 503 when the job
 * was recorded but could not be queued.
 */
export function startMetadataJob(matchId: string, signal?: AbortSignal): Promise<MatchDetail> {
  // No body and no headers, so this stays a CORS simple request.
  return request<MatchDetail>(`/api/matches/${encodeURIComponent(matchId)}/metadata-jobs`, {
    method: "POST",
    signal,
  });
}

/**
 * Create a match by uploading its video. The field name must be `file`: it is
 * what the API's multipart parameter is called.
 */
export function createMatch(file: File, signal?: AbortSignal): Promise<MatchDetail> {
  const form = new FormData();
  form.append("file", file);
  // Content-Type is deliberately not set: the browser has to add the multipart
  // boundary itself, and setting it by hand produces a body the server cannot
  // parse. multipart/form-data is also CORS-safelisted, so this stays a simple
  // request with no preflight.
  return request<MatchDetail>("/api/matches", { method: "POST", body: form, signal });
}
