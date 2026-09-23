/** Turning API values into something readable. */

/**
 * An absolute timestamp, rendered in the viewer's locale.
 *
 * Deliberately not "3 minutes ago": a relative time computed during server
 * rendering is wrong by the time it reaches the browser, and this app has no
 * reason to re-render just to keep a label honest.
 */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** How long a job took, once it has both ends. */
export function formatDuration(startIso: string | null, endIso: string | null): string | null {
  if (startIso === null || endIso === null) return null;
  const milliseconds = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null;
  if (milliseconds < 1000) return `${milliseconds} ms`;
  const seconds = milliseconds / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}
