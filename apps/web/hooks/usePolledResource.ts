"use client";

/**
 * Re-fetch something on a timer until it is finished.
 *
 * The requirements that shaped this:
 *
 * - Requests must never overlap, so the next fetch is scheduled only after the
 *   previous one settles. A fixed `setInterval` would stack requests the moment
 *   one took longer than the period.
 * - A blip must not look like a failure. The last good value stays on screen
 *   and `staleError` is raised alongside it, so a dropped packet never replaces
 *   a rendered job with an error page. Repeated failures back off.
 * - Polling must stop when the answer can no longer change, and on unmount.
 * - A person must be able to ask for a fresh value themselves.
 *
 * The whole loop lives inside one effect, with its timer and abort controller
 * as ordinary local variables. That is what makes cleanup airtight: the
 * returned teardown closes over exactly the loop it started, so unmounting,
 * navigating away, or React re-running the effect in development StrictMode
 * each cancel their own loop and nothing else.
 *
 * `fetcher` and `shouldContinue` are effect dependencies, so callers must
 * memoize them; an inline arrow would restart the loop on every render.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";

export interface PolledResource<T> {
  /** The last value successfully fetched, or null before the first one. */
  data: T | null;
  /** True only while the very first fetch is in flight. */
  isLoading: boolean;
  /**
   * True while a fetch the person asked for is in flight.
   *
   * Deliberately not "any request in flight": a background poll must not
   * disable the refresh button or start a spinner every few seconds, which is
   * movement nobody asked for and which a screen reader may announce.
   */
  isRefreshing: boolean;
  /** Set when there is nothing to show at all. */
  error: ApiError | null;
  /** Set when a refresh failed but `data` is still on screen. */
  staleError: ApiError | null;
  /** True while the timer is running. */
  isPolling: boolean;
  /** Fetch now, resetting the backoff. */
  refresh: () => void;
}

export interface PollOptions<T> {
  /** Milliseconds between polls. */
  intervalMs?: number;
  /** Longest gap after repeated failures. */
  maxIntervalMs?: number;
  /**
   * Return true while the value could still change, so the hook should ask
   * again. Omitted means "fetch once", which is the base case; polling is what
   * you get by supplying a predicate. Must be memoized when supplied.
   */
  shouldContinue?: (data: T) => boolean;
}

/** The default predicate: one fetch, then stop. */
const FETCH_ONCE = () => false;

function asApiError(cause: unknown): ApiError {
  return cause instanceof ApiError
    ? cause
    : new ApiError("Something unexpected went wrong.", { code: "unknown" });
}

export function usePolledResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  { intervalMs = 2000, maxIntervalMs = 30000, shouldContinue = FETCH_ONCE }: PollOptions<T>,
): PolledResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [staleError, setStaleError] = useState<ApiError | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isPolling, setIsPolling] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);

  /**
   * Whether a value has ever arrived, across effect runs.
   *
   * A loop-local would be reset every time the effect restarts -- which is
   * what `refresh()` does -- so the first failed refresh would look like "we
   * have nothing" and replace the rendered page with an error.
   */
  const everLoaded = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let failures = 0;

    async function tick(): Promise<void> {
      const own = new AbortController();
      controller = own;

      try {
        const value = await fetcher(own.signal);
        if (cancelled) return;

        failures = 0;
        everLoaded.current = true;
        setData(value);
        setError(null);
        setStaleError(null);

        if (shouldContinue(value)) {
          setIsPolling(true);
          timer = setTimeout(() => void tick(), intervalMs);
        } else {
          setIsPolling(false);
        }
      } catch (cause) {
        // An abort is this hook tidying up after itself, not a failure.
        if (cancelled || own.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;

        const failure = asApiError(cause);
        failures += 1;

        // A 404 will not fix itself; stop and surface it.
        if (!failure.isRetryable) {
          setError(failure);
          setStaleError(null);
          setIsPolling(false);
          return;
        }

        // Keep whatever is on screen and say so, rather than blanking the page.
        setStaleError(failure);
        if (!everLoaded.current) setError(failure);
        setIsPolling(true);
        timer = setTimeout(
          () => void tick(),
          Math.min(intervalMs * 2 ** (failures - 1), maxIntervalMs),
        );
      } finally {
        if (!cancelled) setIsRefreshing(false);
      }
    }

    void tick();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
      controller?.abort();
    };
  }, [fetcher, shouldContinue, intervalMs, maxIntervalMs, refreshCount]);

  // Setting the flag here rather than inside the effect means the button
  // responds to the click itself, not one render later.
  const refresh = useCallback(() => {
    setIsRefreshing(true);
    setRefreshCount((count) => count + 1);
  }, []);

  return {
    data,
    isLoading: data === null && error === null,
    isRefreshing,
    error,
    staleError,
    isPolling,
    refresh,
  };
}
