"use client";

import Link from "next/link";
import { useId, useRef, useState } from "react";
import { ApiError, uploadVideo } from "@/lib/api";
import {
  ACCEPTED_EXTENSIONS,
  ACCEPT_ATTRIBUTE,
  MAX_UPLOAD_BYTES,
  formatBytes,
  validateVideoFile,
} from "@/lib/files";
import type { VideoDetail } from "@/lib/types";
import { Spinner } from "./Spinner";

type Phase = "idle" | "uploading" | "done";

export function UploadForm() {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;

  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [created, setCreated] = useState<VideoDetail | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const successRef = useRef<HTMLHeadingElement>(null);
  // A ref, not the `phase` state: two Enter presses can both run before React
  // re-renders, and `disabled` alone would not have caught the second.
  const inFlight = useRef(false);
  // Drag events fire for child elements too, so a plain boolean flickers.
  const dragDepth = useRef(0);

  function choose(next: File | null) {
    setFile(next);
    setProblem(validateVideoFile(next));
    setCreated(null);
    setPhase("idle");
  }

  function onDragOver(event: React.DragEvent) {
    // Without this the drop event never fires and the browser navigates away
    // to open the file, losing the page.
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function onDragEnter(event: React.DragEvent) {
    event.preventDefault();
    dragDepth.current += 1;
    setIsDragging(true);
  }

  function onDragLeave() {
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setIsDragging(false);
    }
  }

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    dragDepth.current = 0;
    setIsDragging(false);
    const dropped = event.dataTransfer.files?.[0] ?? null;
    if (dropped) choose(dropped);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;

    const invalid = validateVideoFile(file);
    if (invalid !== null || file === null) {
      setProblem(invalid);
      // Land on the control that needs fixing. The error is referenced by
      // aria-describedby, so it is read as part of the focus announcement --
      // which is also why it must not be a live region as well.
      inputRef.current?.focus();
      return;
    }

    inFlight.current = true;
    setPhase("uploading");
    setProblem(null);
    try {
      const video = await uploadVideo(file);
      setCreated(video);
      setPhase("done");
      // Put the keyboard on the result rather than leaving it on a button that
      // has just been replaced.
      requestAnimationFrame(() => successRef.current?.focus());
    } catch (cause) {
      setPhase("idle");
      setProblem(messageFor(cause));
      inputRef.current?.focus();
    } finally {
      inFlight.current = false;
    }
  }

  const isUploading = phase === "uploading";

  return (
    <>
      {/*
        Mounted empty on first paint and never conditionally rendered:
        assistive technology registers a live region when it is inserted and
        announces later changes, so a region that arrives with its first
        message is frequently missed.
      */}
      <p role="status" className="sr-only">
        {isUploading
          ? "Uploading your video. Large files can take several minutes."
          : phase === "done"
            ? "Upload complete. A processing job was created."
            : ""}
      </p>

      <form onSubmit={onSubmit} noValidate>
        <label
          htmlFor={inputId}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center sm:min-h-44 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent ${
            isDragging ? "border-accent bg-surface" : "border-border-subtle"
          }`}
        >
          <input
            ref={inputRef}
            id={inputId}
            name="file"
            type="file"
            accept={ACCEPT_ATTRIBUTE}
            // sr-only, never `hidden` or `display:none`: those take the input
            // out of the tab order and the accessibility tree, which is the
            // usual way this pattern breaks for keyboard users.
            className="sr-only"
            aria-describedby={problem ? `${hintId} ${errorId}` : hintId}
            aria-invalid={problem ? true : undefined}
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
          />
          <span className="font-medium">Choose a video file</span>
          <span aria-hidden="true" className="hidden text-sm text-muted sm:block">
            or drag and drop it here
          </span>
        </label>

        <p id={hintId} className="mt-2 text-sm text-muted">
          {ACCEPTED_EXTENSIONS.join(", ")} up to {formatBytes(MAX_UPLOAD_BYTES)}.
          {file ? ` Selected: ${file.name} (${formatBytes(file.size)}).` : ""}
        </p>

        {problem ? (
          <p id={errorId} className="mt-2 text-sm font-medium text-red-700 dark:text-red-300">
            <span aria-hidden="true">⚠ </span>
            {problem}
          </p>
        ) : null}

        <button
          type="submit"
          // aria-disabled rather than disabled: a focused element that becomes
          // disabled drops focus to the body and the keyboard loses its place.
          aria-disabled={isUploading}
          className="focus-ring mt-4 inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent px-4 font-medium text-background aria-disabled:cursor-default aria-disabled:opacity-60"
        >
          {isUploading ? <Spinner /> : null}
          {isUploading ? "Uploading…" : "Upload video"}
        </button>

        {isUploading ? (
          <p className="mt-2 text-sm text-muted">
            Keep this tab open. A large match file can take several minutes.
          </p>
        ) : null}
      </form>

      {created ? (
        <div className="mt-6 rounded-lg border border-border-subtle bg-surface p-4">
          <h2
            ref={successRef}
            tabIndex={-1}
            className="focus-ring scroll-mt-4 text-lg font-semibold"
          >
            Upload complete
          </h2>
          <p className="mt-1 text-sm text-muted">
            {created.original_filename} was stored and a processing job was created.
          </p>
          <Link
            href={`/videos/${created.id}`}
            className="focus-ring mt-3 inline-flex min-h-11 items-center rounded-lg border border-border-subtle px-4 font-medium"
          >
            View processing status
          </Link>
        </div>
      ) : null}
    </>
  );
}

/**
 * The sentence to show for a failed upload.
 *
 * The 503 is the interesting one: the video really was stored and really does
 * exist in the list, and only the queueing failed. Calling that "upload
 * failed" would send someone off to upload it a second time.
 */
function messageFor(cause: unknown): string {
  if (!(cause instanceof ApiError)) return "Something went wrong while uploading. Try again.";
  if (cause.status === 503) {
    return "Your video was saved, but it could not be queued for processing. It appears in Videos with a failed job.";
  }
  if (cause.status === 507) {
    return "The server does not have enough free space for this upload right now.";
  }
  // 413 and 415 already carry a specific, readable sentence from the server.
  return cause.message;
}
