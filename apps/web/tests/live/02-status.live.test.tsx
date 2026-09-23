/**
 * The real components polling the real API.
 *
 * Runs in jsdom, where GET requests work normally, and reads the video that
 * 01-upload just created. Together the two files cover what a browser check
 * would: an upload is accepted, the status page renders it, the status
 * changes, and a terminal state stops the polling.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, expect, it, vi } from "vitest";
import { VideoListView } from "@/components/VideoListView";
import { VideoStatusView } from "@/components/VideoStatusView";
import { API_BASE_URL, listVideos } from "@/lib/api";
import type { VideoSummary } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

let newest: VideoSummary;

beforeAll(async () => {
  const { videos } = await listVideos().catch(() => ({ videos: [] as VideoSummary[] }));
  if (videos.length === 0) {
    throw new Error(
      `No videos at ${API_BASE_URL}. Run the whole live suite so the upload test runs first.`,
    );
  }
  newest = videos[0];
});

it("renders the newest upload and follows its job to a terminal state", async () => {
  render(<VideoStatusView videoId={newest.id} pollIntervalMs={300} />);

  // Something real is on screen rather than a permanent skeleton.
  await waitFor(() => expect(screen.getByTestId("status-badge")).toBeInTheDocument(), {
    timeout: 15000,
  });

  // With the worker running this becomes `ready`. Going straight there from
  // `queued` without an observed `running` is normal: the placeholder
  // processor finishes in milliseconds.
  await waitFor(
    () =>
      expect(
        screen.getByRole("heading", { name: /file check complete|processing failed/i }),
      ).toBeInTheDocument(),
    { timeout: 30000 },
  );

  expect(screen.getByText(/does not analyse video yet/i)).toBeInTheDocument();
  // Polling must stop once the answer cannot change.
  await waitFor(() => expect(screen.getByText(/checking has stopped/i)).toBeInTheDocument());
}, 60000);

it("lists that upload with a status badge", async () => {
  render(<VideoListView />);
  // findAllBy, not findBy: every run of this suite uploads another file with
  // the same name, so by the second run the name is not unique.
  const rows = await screen.findAllByText(
    newest.original_filename,
    {},
    { timeout: 15000 },
  );
  expect(rows.length).toBeGreaterThan(0);
  expect(screen.getAllByTestId("status-badge").length).toBeGreaterThanOrEqual(rows.length);
}, 30000);
