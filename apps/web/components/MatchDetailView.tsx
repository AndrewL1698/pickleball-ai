"use client";

import Link from "next/link";
import { useCallback, useId, useRef, useState } from "react";
import { ApiError, getMatch, startMetadataJob } from "@/lib/api";
import { formatBytes } from "@/lib/files";
import {
  formatAverageFps,
  formatDuration,
  formatMediaDuration,
  formatTimestamp,
} from "@/lib/format";
import { MATCH_STATUS_COPY, STATUS_COPY, isFinished } from "@/lib/status";
import type { Job, MatchDetail, Video } from "@/lib/types";
import { usePolledResource } from "@/hooks/usePolledResource";
import { Notice } from "./Notice";
import { RefreshButton } from "./RefreshButton";
import { ScopeNotice } from "./ScopeNotice";
import { Spinner } from "./Spinner";
import { MatchStatusBadge, StatusBadge } from "./StatusBadge";

/**
 * One match: its status, its video, and the state of its processing job.
 *
 * It polls `GET /api/matches/{id}` rather than `GET /api/jobs/{id}` because
 * that one response carries the match, its video and every job for it, so the
 * page needs a single request per tick and there is no jobs-list endpoint to
 * miss.
 */
export function MatchDetailView({
  matchId,
  pollIntervalMs = 2000,
}: {
  matchId: string;
  /** Exposed so tests can drive the loop faster than a person would see. */
  pollIntervalMs?: number;
}) {
  const fetcher = useCallback(
    (signal: AbortSignal) => getMatch(matchId, signal),
    [matchId],
  );

  // Keep going while the job could still change. A match with no job is never
  // going to grow one, so that stops too.
  const shouldContinue = useCallback(
    (match: MatchDetail) =>
      match.latest_job !== null && !isFinished(match.latest_job.status),
    [],
  );

  const { data, error, staleError, isLoading, isRefreshing, isPolling, refresh } =
    usePolledResource<MatchDetail>(fetcher, {
      intervalMs: pollIntervalMs,
      shouldContinue,
    });

  const job = data?.latest_job ?? null;
  const copy = job ? STATUS_COPY[job.status] : null;

  const title = data ? data.name : "Match";
  const video = data?.video ?? null;
  const announcement = copy?.announcement ?? "";

  if (isLoading) {
    return (
      <Shell title={title} announcement={announcement}>
        <p className="flex items-center gap-2 text-muted">
          <Spinner />
          Loading match…
        </p>
      </Shell>
    );
  }

  if (error !== null || data === null) {
    const isMissing = error?.status === 404 || error?.status === 422;
    return (
      <Shell title={title} announcement={announcement}>
        <div className="space-y-4">
          <Notice
            tone="error"
            role="alert"
            title={isMissing ? "We could not find that match." : "We could not load this match."}
            code={error?.code}
          >
            <p>
              {isMissing
                ? "It may have been removed, or the link may be wrong."
                : (error?.message ?? "Something went wrong.")}
            </p>
          </Notice>
          <Link
            href="/matches"
            className="focus-ring inline-flex min-h-11 items-center rounded-lg border border-border-subtle px-4 font-medium"
          >
            Back to matches
          </Link>
        </div>
      </Shell>
    );
  }

  return (
    <Shell title={title} announcement={announcement}>
      <section aria-labelledby="match-heading">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="match-heading" className="text-lg font-semibold">
            Match status
          </h2>
          <MatchStatusBadge status={data.status} />
        </div>
        <p className="mt-2 text-muted">{MATCH_STATUS_COPY[data.status].description}</p>
      </section>

      <ScopeNotice />

      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold tracking-tight">
            {copy?.heading ?? "No processing job"}
          </h2>
          {job ? <StatusBadge status={job.status} /> : null}
        </div>
        <p className="mt-2 text-muted">{copy?.description ?? "This match has no job."}</p>
      </header>

      {job?.status === "failed" ? (
        <Notice tone="error" title="What went wrong" code={job.error_code}>
          <p>{job.error_message ?? "The job failed."}</p>
        </Notice>
      ) : null}

      <section aria-labelledby="upload-heading">
        <h2 id="upload-heading" className="text-lg font-semibold">
          Video
        </h2>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {video ? (
            <>
              <Field label="File name" value={video.original_filename} />
              <Field label="Size" value={formatBytes(video.byte_size)} />
              <Field label="Type" value={video.content_type} />
              <Field label="Uploaded" value={formatTimestamp(video.created_at)} />
            </>
          ) : (
            <Field label="File" value="No video is attached to this match." />
          )}
          <Field
            label="Recorded"
            value={data.recorded_at ? formatTimestamp(data.recorded_at) : "Not set"}
          />
          {job ? (
            <>
              <Field label="Job created" value={formatTimestamp(job.created_at)} />
              <Field
                label="Processing time"
                value={formatDuration(job.started_at, job.finished_at) ?? "Unknown"}
              />
            </>
          ) : null}
        </dl>
      </section>

      <MetadataSection
        matchId={data.id}
        video={video}
        job={job}
        canExtract={data.can_extract_metadata}
        onStarted={refresh}
      />

      <section aria-labelledby="checks-heading">
        <h2 id="checks-heading" className="text-lg font-semibold">
          Status checks
        </h2>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <RefreshButton label="Refresh" isRefreshing={isRefreshing} onRefresh={refresh} />
          <p className="text-sm text-muted">
            {isPolling
              ? "Checking automatically every few seconds."
              : "This job has finished, so checking has stopped."}
          </p>
        </div>

        {/*
          A refresh that failed while a job is still on screen is a transport
          problem, not a job problem. Saying so -- rather than flipping the
          badge to Failed -- is the difference between the two.
        */}
        {staleError !== null ? (
          <Notice tone="error" role="status" title="Could not check for updates just now.">
            <p>
              {staleError.message} Showing the last known status; it will try again shortly.
            </p>
          </Notice>
        ) : null}
      </section>

      {data.jobs.length > 1 ? (
        <section aria-labelledby="attempts-heading">
          <h2 id="attempts-heading" className="text-lg font-semibold">
            Earlier attempts
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            {data.jobs.slice(0, -1).map((attempt) => (
              <li key={attempt.id} className="flex flex-wrap items-center gap-2">
                <StatusBadge status={attempt.status} />
                <time dateTime={attempt.created_at} className="text-muted">
                  {formatTimestamp(attempt.created_at)}
                </time>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Shell>
  );
}

/**
 * The decoded metadata, or an honest account of why there is none.
 *
 * Nothing here is rendered as a blank or a zero: either the server has the
 * value, or the section says which of the reasons for not having it applies.
 * The extraction action appears only when the server says it would accept it
 * (`can_extract_metadata`), so the page never offers a button that answers 409.
 */
function MetadataSection({
  matchId,
  video,
  job,
  canExtract,
  onStarted,
}: {
  matchId: string;
  video: Video | null;
  job: Job | null;
  canExtract: boolean;
  onStarted: () => void;
}) {
  const metadata = video?.metadata ?? null;
  const isActive = job?.status === "queued" || job?.status === "running";

  return (
    <section aria-labelledby="metadata-heading">
      <h2 id="metadata-heading" className="text-lg font-semibold">
        Video metadata
      </h2>
      {metadata ? (
        <>
          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <Field label="Resolution (as displayed)" value={`${metadata.width} × ${metadata.height}`} />
            <Field
              label="Rotation"
              value={
                metadata.rotation_degrees === 0
                  ? "None"
                  : `${metadata.rotation_degrees}°, applied to the resolution above`
              }
            />
            <Field
              label="Duration (estimate)"
              value={formatMediaDuration(metadata.duration_seconds)}
            />
            <Field label="Frame rate" value={formatAverageFps(metadata.average_fps)} />
            <Field label="Frames" value={metadata.frame_count.toLocaleString()} />
            <Field label="Codec" value={metadata.codec ?? "Not named by the file"} />
            <Field label="Extracted" value={formatTimestamp(metadata.extracted_at)} />
          </dl>
          <p className="mt-3 text-sm text-muted">
            The frame rate is an average. Phone video often varies its frame rate, so the
            duration and any time worked out from frame numbers are approximate.
          </p>
        </>
      ) : (
        <div className="mt-3 space-y-3">
          <p className="text-sm text-muted">{missingMetadataReason(video, job, isActive)}</p>
          {canExtract ? (
            <ExtractMetadataButton
              matchId={matchId}
              label={job?.status === "failed" ? "Try again" : "Extract metadata"}
              onStarted={onStarted}
            />
          ) : null}
        </div>
      )}
    </section>
  );
}

function missingMetadataReason(video: Video | null, job: Job | null, isActive: boolean): string {
  if (video === null) return "There is no video to read metadata from.";
  if (isActive) return "Metadata is being extracted. It appears here when the job finishes.";
  if (job?.status === "failed") return "No metadata was saved, because processing failed.";
  if (job?.status === "ready") {
    // Only matches uploaded before extraction existed get here: a job that
    // finishes now saves metadata in the same step.
    return "Metadata has not been extracted. This video was processed before metadata extraction was added.";
  }
  return "Metadata has not been extracted for this video.";
}

/**
 * Asks the API for a new metadata job. The same in-flight rules as the upload
 * form: a ref blocks a double submission that React would not re-render in
 * time to stop, and `aria-disabled` keeps focus on the button.
 */
function ExtractMetadataButton({
  matchId,
  label,
  onStarted,
}: {
  matchId: string;
  label: string;
  onStarted: () => void;
}) {
  const errorId = useId();
  const inFlight = useRef(false);
  const [isStarting, setIsStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function start() {
    if (inFlight.current) return;
    inFlight.current = true;
    setIsStarting(true);
    setProblem(null);
    try {
      await startMetadataJob(matchId);
      // Reload the match: the new job is queued, so polling resumes.
      onStarted();
    } catch (cause) {
      // The API's sentences for 409 and 503 are fixed and safe to show.
      setProblem(
        cause instanceof ApiError ? cause.message : "Something went wrong. Try again.",
      );
    } finally {
      inFlight.current = false;
      setIsStarting(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        aria-disabled={isStarting}
        aria-describedby={problem ? errorId : undefined}
        onClick={() => void start()}
        className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent px-4 font-medium text-background aria-disabled:cursor-default aria-disabled:opacity-60"
      >
        {isStarting ? <Spinner /> : null}
        {isStarting ? "Starting…" : label}
      </button>
      {problem ? (
        <p id={errorId} className="mt-2 text-sm font-medium text-red-700 dark:text-red-300">
          <span aria-hidden="true">⚠ </span>
          {problem}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The frame every state renders inside.
 *
 * Module level, not declared inside `MatchDetailView`: a component defined in
 * a render body is a new type on every render, so React would remount this
 * subtree -- and destroy and re-insert the live region -- on every poll.
 */
function Shell({
  title,
  announcement,
  children,
}: {
  title: string;
  announcement: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      {/*
        A heading in every state, so the page is never heading-less, and a live
        region present from the first paint: assistive technology registers a
        region when it is inserted and announces later changes, so one that
        arrives with its first message is frequently missed.

        The announcement depends only on the status -- never on a timestamp or
        a counter. React leaves an unchanged text node alone, so an unchanged
        status re-announces nothing, however often it is polled.
      */}
      <h1 className="text-2xl font-semibold tracking-tight break-words">{title}</h1>
      <p role="status" className="sr-only">
        {announcement}
      </p>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="break-words font-medium">{value}</dd>
    </div>
  );
}
