import type { Metadata } from "next";
import { UploadForm } from "@/components/UploadForm";

export const metadata: Metadata = {
  title: "Upload a video",
};

/**
 * A Server Component wrapper around the client form.
 *
 * `metadata` cannot be exported from a `"use client"` module, so each route is
 * a thin server shell holding the metadata and rendering the interactive part.
 */
export default function UploadPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Upload a video</h1>
        <p className="mt-2 text-muted">
          Add a pickleball match recording. It is stored and queued for a processing job that
          you can follow.
        </p>
      </header>
      <UploadForm />
    </div>
  );
}
