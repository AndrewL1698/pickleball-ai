"use client";

import { useEffect } from "react";

/**
 * Route-level error boundary. Must be a Client Component.
 *
 * The recovery prop is `retry` as of Next 16.3: it re-fetches and re-renders
 * the segment, where the older `reset` only cleared the error state.
 */
export default function RouteError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Something went wrong</h1>
      <p className="text-muted">
        This page could not be displayed. The API may be unavailable.
      </p>
      <button
        type="button"
        onClick={() => retry()}
        className="focus-ring inline-flex min-h-11 items-center rounded-lg border border-border-subtle px-4 font-medium"
      >
        Try again
      </button>
    </div>
  );
}
