"use client";

import Link from "next/link";
import { useCallback } from "react";
import { type ApiError, listMatches } from "@/lib/api";
import { formatBytes } from "@/lib/files";
import { formatTimestamp } from "@/lib/format";
import type { MatchList } from "@/lib/types";
import { usePolledResource } from "@/hooks/usePolledResource";
import { Notice } from "./Notice";
import { RefreshButton } from "./RefreshButton";
import { Spinner } from "./Spinner";
import { MatchStatusBadge } from "./StatusBadge";

export function MatchListView() {
  const fetcher = useCallback((signal: AbortSignal) => listMatches(signal), []);
  // No `shouldContinue`, so the hook fetches once and stops. The list wants
  // everything else it provides: cancellation on unmount, the keep-the-last-
  // value error handling, and a manual retry.
  const { data, error, isLoading, isRefreshing, refresh } =
    usePolledResource<MatchList>(fetcher, {});

  return (
    <>
      {/*
        Mounted in every branch, so assistive technology registers the region
        before it has anything to say. A region inserted together with its
        first message is frequently not announced.
      */}
      <p role="status" className="sr-only">
        {announcement(isLoading, error, data?.matches.length)}
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
  data: MatchList | null;
  error: ApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  refresh: () => void;
}) {
  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-muted">
        <Spinner />
        Loading your matches…
      </p>
    );
  }

  if (error !== null || data === null) {
    return (
      <div className="space-y-3">
        <Notice
          tone="error"
          role="alert"
          title="We could not load your matches."
          code={error?.code}
        >
          <p>{error?.message ?? "Something went wrong."}</p>
        </Notice>
        <RefreshButton label="Try again" isRefreshing={isRefreshing} onRefresh={refresh} />
      </div>
    );
  }

  if (data.matches.length === 0) {
    // Neutral, not an error: an empty list is what a new install looks like.
    return (
      <div className="rounded-lg border border-border-subtle bg-surface p-6 text-center">
        <h2 className="font-semibold">No matches yet</h2>
        <p className="mt-1 text-sm text-muted">
          Upload a match recording to create your first match.
        </p>
        <Link
          href="/"
          className="focus-ring mt-4 inline-flex min-h-11 items-center rounded-lg bg-accent px-4 font-medium text-background"
        >
          Upload a match
        </Link>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {data.matches.map((match) => (
        <li
          key={match.id}
          className="relative isolate rounded-lg border border-border-subtle p-4 md:grid md:grid-cols-[1fr_auto] md:items-center md:gap-4"
        >
          <div className="min-w-0">
            <h2 className="font-medium break-words">
              {/*
                A link, not a clickable row: navigation has to work with the
                keyboard, and people expect to open it in a new tab. The
                stretched ::after makes the whole card clickable while leaving
                exactly one tab stop, named after the match.
              */}
              <Link
                href={`/matches/${match.id}`}
                className="focus-ring rounded-sm after:absolute after:inset-0 after:rounded-lg"
              >
                {match.name}
              </Link>
            </h2>
            <p className="mt-1 text-sm text-muted">
              <time dateTime={match.created_at}>{formatTimestamp(match.created_at)}</time>
              {match.video ? (
                <>
                  {" · "}
                  <span className="break-all">{match.video.original_filename}</span>
                  {" · "}
                  {formatBytes(match.video.byte_size)}
                </>
              ) : (
                " · No video"
              )}
            </p>
          </div>
          <div className="mt-2 md:mt-0">
            {/* The match status, not the job's: it is the summary a person acts on. */}
            <MatchStatusBadge status={match.status} />
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
  if (isLoading) return "Loading matches.";
  if (error !== null || count === undefined) return "Could not load your matches.";
  if (count === 0) return "No matches yet.";
  return `${count} ${count === 1 ? "match" : "matches"} loaded.`;
}
