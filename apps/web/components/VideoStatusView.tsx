"use client";

import Link from "next/link";
import { useCallback } from "react";
import { getVideo } from "@/lib/api";
import { formatBytes } from "@/lib/files";
import { formatDuration, formatTimestamp } from "@/lib/format";
import { STATUS_COPY, isFinished } from "@/lib/status";
import type { VideoDetail } from "@/lib/types";
import { usePolledResource } from "@/hooks/usePolledResource";
import { Notice } from "./Notice";
import { PlaceholderNotice } from "./PlaceholderNotice";
import { RefreshButton } from "./RefreshButton";
import { Spinner } from "./Spinner";
import { StatusBadge } from "./StatusBadge";

/**
 * One video and the state of its job.
 *
 * It polls `GET /api/videos/{id}` rather than `GET /api/jobs/{id}` because that
 * one response carries the video and every job for it, so the page needs a
 * single request per tick and there is no jobs-list endpoint to miss.
 */
export function VideoStatusView({
  videoId,
  pollIntervalMs = 2000,
}: {
  videoId: string;
  /** Exposed so tests can drive the loop faster than a person would see. */
  pollIntervalMs?: number;
}) {
  const fetcher = useCallback(
    (signal: AbortSignal) => getVideo(videoId, signal),
    [videoId],
  );

  // Keep going while the job could still change. A video with no job is never
  // going to grow one, so that stops too.
  const shouldContinue = useCallback(
    (video: VideoDetail) =>
      video.latest_job !== null && !isFinished(video.latest_job.status),
    [],
  );

  const { data, error, staleError, isLoading, isRefreshing, isPolling, refresh } =
    usePolledResource<VideoDetail>(fetcher, {
      intervalMs: pollIntervalMs,
      shouldContinue,
    });

  const job = data?.latest_job ?? null;
  const copy = job ? STATUS_COPY[job.status] : null;

  const title = data ? data.original_filename : "Processing job";
  const announcement = copy?.announcement ?? "";

  if (isLoading) {
    return (
      <Shell title={title} announcement={announcement}>
        <p className="flex items-center gap-2 text-muted">
          <Spinner />
          Loading job…
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
            title={isMissing ? "We could not find that video." : "We could not load this video."}
            code={error?.code}
          >
            <p>
              {isMissing
                ? "It may have been removed, or the link may be wrong."
                : (error?.message ?? "Something went wrong.")}
            </p>
          </Notice>
          <Link
            href="/videos"
            className="focus-ring inline-flex min-h-11 items-center rounded-lg border border-border-subtle px-4 font-medium"
          >
            Back to videos
          </Link>
        </div>
      </Shell>
    );
  }

  return (
    <Shell title={title} announcement={announcement}>
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold tracking-tight">
            {copy?.heading ?? "No processing job"}
          </h2>
          {job ? <StatusBadge status={job.status} /> : null}
        </div>
        <p className="mt-2 text-muted">{copy?.description ?? "This video has no job."}</p>
      </header>

      <PlaceholderNotice />

      {job?.status === "failed" ? (
        <Notice tone="error" title="What went wrong" code={job.error_code}>
          <p>{job.error_message ?? "The job failed."}</p>
        </Notice>
      ) : null}

      <section aria-labelledby="upload-heading">
        <h2 id="upload-heading" className="text-lg font-semibold">
          Upload
        </h2>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Field label="File name" value={data.original_filename} />
          <Field label="Size" value={formatBytes(data.byte_size)} />
          <Field label="Type" value={data.content_type} />
          <Field label="Uploaded" value={formatTimestamp(data.created_at)} />
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
        {/*
          The server stores no duration, resolution or frame rate yet, so this
          says so rather than rendering empty cells that read as "zero".
        */}
        <p className="mt-3 text-sm text-muted">
          Video duration, resolution and frame rate are not extracted yet.
        </p>
      </section>

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
 * The frame every state renders inside.
 *
 * Module level, not declared inside `VideoStatusView`: a component defined in
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
