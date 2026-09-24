"use client";

import Link from "next/link";
import { useCallback } from "react";
import { type ApiError, listVideos } from "@/lib/api";
import { formatBytes } from "@/lib/files";
import { formatTimestamp } from "@/lib/format";
import type { VideoList } from "@/lib/types";
import { usePolledResource } from "@/hooks/usePolledResource";
import { Notice } from "./Notice";
import { RefreshButton } from "./RefreshButton";
import { Spinner } from "./Spinner";
import { StatusBadge } from "./StatusBadge";

export function VideoListView() {
  const fetcher = useCallback((signal: AbortSignal) => listVideos(signal), []);
  // No `shouldContinue`, so the hook fetches once and stops. The list wants
  // everything else it provides: cancellation on unmount, the keep-the-last-
  // value error handling, and a manual retry.
  const { data, error, isLoading, isRefreshing, refresh } =
    usePolledResource<VideoList>(fetcher, {});

  return (
    <>
      {/*
        Mounted in every branch, so assistive technology registers the region
        before it has anything to say. A region inserted together with its
        first message is frequently not announced.
      */}
      <p role="status" className="sr-only">
        {announcement(isLoading, error, data?.videos.length)}
      </p>
      <Body
        data={data}
        error={error}
        isLoading={isLoading}
        isRefreshing={isRefreshing}
        refresh={refresh}
      />
    </>
  );
}

function Body({
  data,
  error,
  isLoading,
  isRefreshing,
  refresh,
}: {
  data: VideoList | null;
  error: ApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  refresh: () => void;
}) {
  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-muted">
        <Spinner />
        Loading your videos…
      </p>
    );
  }

  if (error !== null || data === null) {
    return (
      <div className="space-y-3">
        <Notice
          tone="error"
          role="alert"
          title="We could not load your videos."
          code={error?.code}
        >
          <p>{error?.message ?? "Something went wrong."}</p>
        </Notice>
        <RefreshButton label="Try again" isRefreshing={isRefreshing} onRefresh={refresh} />
      </div>
    );
  }

  if (data.videos.length === 0) {
    // Neutral, not an error: an empty list is what a new install looks like.
    return (
      <div className="rounded-lg border border-border-subtle bg-surface p-6 text-center">
        <h2 className="font-semibold">No videos yet</h2>
        <p className="mt-1 text-sm text-muted">Upload a match recording to get started.</p>
        <Link
          href="/"
          className="focus-ring mt-4 inline-flex min-h-11 items-center rounded-lg bg-accent px-4 font-medium text-background"
        >
          Upload a video
        </Link>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {data.videos.map((video) => (
        <li
          key={video.id}
          className="relative isolate rounded-lg border border-border-subtle p-4 md:grid md:grid-cols-[1fr_auto] md:items-center md:gap-4"
        >
          <div className="min-w-0">
            <h2 className="font-medium break-words">
              {/*
                A link, not a clickable row: navigation has to work with the
                keyboard, and people expect to open it in a new tab. The
                stretched ::after makes the whole card clickable while leaving
                exactly one tab stop, named after the file.
              */}
              <Link
                href={`/videos/${video.id}`}
                className="focus-ring rounded-sm after:absolute after:inset-0 after:rounded-lg"
              >
                {video.original_filename}
              </Link>
            </h2>
            <p className="mt-1 text-sm text-muted">
              <time dateTime={video.created_at}>{formatTimestamp(video.created_at)}</time>
              {" · "}
              {formatBytes(video.byte_size)}
            </p>
          </div>
          <div className="mt-2 md:mt-0">
            {video.latest_job ? (
              <StatusBadge status={video.latest_job.status} />
            ) : (
              <span className="text-sm text-muted">No job</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** What a screen reader hears as the list moves between its states. */
function announcement(
  isLoading: boolean,
  error: ApiError | null,
  count: number | undefined,
): string {
  if (isLoading) return "Loading videos.";
  if (error !== null || count === undefined) return "Could not load your videos.";
  if (count === 0) return "No videos yet.";
  return `${count} ${count === 1 ? "video" : "videos"} loaded.`;
}
