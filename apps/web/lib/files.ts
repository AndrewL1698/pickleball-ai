/**
 * Client-side upload validation.
 *
 * This is a courtesy, not a control: it tells someone their file is wrong
 * before they wait for a 2 GB upload to fail. The server re-checks all of it,
 * and the server's answer is the one that counts.
 *
 * The limits mirror apps/api/src/pickleball_api/config.py. They are read from
 * the environment so the two can be changed together.
 */

export const ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".m4v"] as const;

/** The `accept` attribute. A hint to the file picker, never a validation. */
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(",");

const DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024; // 2 GiB, matching the API

export const MAX_UPLOAD_BYTES = readMaxBytes();

function readMaxBytes(): number {
  const configured = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_BYTES);
  return Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_MAX_BYTES;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

/**
 * Why this file cannot be uploaded, or null if it looks fine.
 *
 * The message is written to be shown verbatim next to the input.
 */
export function validateVideoFile(file: File | null): string | null {
  if (file === null) return "Choose a video file to upload.";
  if (file.size === 0) return "That file is empty.";

  const extension = extensionOf(file.name);
  if (!ACCEPTED_EXTENSIONS.includes(extension as (typeof ACCEPTED_EXTENSIONS)[number])) {
    return `${file.name} is not a supported video. Use ${ACCEPTED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `${file.name} is ${formatBytes(file.size)}. The limit is ${formatBytes(
      MAX_UPLOAD_BYTES,
    )}.`;
  }
  return null;
}
