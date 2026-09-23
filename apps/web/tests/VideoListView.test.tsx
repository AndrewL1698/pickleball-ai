/** The four states the list has to handle. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { VideoListView } from "@/components/VideoListView";
import { JOB_READY, VIDEO_SUMMARY, jsonResponse } from "./fixtures";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

it("shows a named loading state while the first request is in flight", async () => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  render(<VideoListView />);
  // A spinner with no accessible name says nothing; the text is the point.
  expect(await screen.findByText(/loading your videos/i)).toBeInTheDocument();
});

it("shows an inviting empty state, not an error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ videos: [], count: 0 })));
  render(<VideoListView />);

  expect(await screen.findByRole("heading", { name: /no videos yet/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /upload a video/i })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("lists videos with their filename, date and latest status", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ videos: [VIDEO_SUMMARY], count: 1 })),
  );
  render(<VideoListView />);

  expect(await screen.findByText("demo.mp4")).toBeInTheDocument();
  expect(screen.getByTestId("status-badge")).toHaveTextContent(/queued/i);
  expect(screen.getByRole("link", { name: "demo.mp4" })).toHaveAttribute(
    "href",
    `/videos/${VIDEO_SUMMARY.id}`,
  );
  // A row must be a real link, so it can be tabbed to and opened in a new tab.
  expect(screen.getByRole("link", { name: "demo.mp4" }).tagName).toBe("A");
});

it("renders each video's own latest status", async () => {
  const second = { ...VIDEO_SUMMARY, id: "other", original_filename: "b.mp4", latest_job: JOB_READY };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ videos: [VIDEO_SUMMARY, second], count: 2 })),
  );
  render(<VideoListView />);

  await screen.findByText("demo.mp4");
  const badges = screen.getAllByTestId("status-badge");
  expect(badges[0]).toHaveTextContent(/queued/i);
  expect(badges[1]).toHaveTextContent(/ready/i);
});

it("announces a load failure and offers a retry that actually refetches", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      jsonResponse({ error_code: "internal_error", detail: "Something went wrong." }, 500),
    )
    .mockResolvedValueOnce(jsonResponse({ videos: [VIDEO_SUMMARY], count: 1 }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<VideoListView />);

  // Unprompted failures announce themselves rather than stealing focus.
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/could not load your videos/i);

  await user.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("demo.mp4")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("tells the user plainly when the API is not running", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
  render(<VideoListView />);
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Is it running\?/i),
  );
});

it("aborts its request when unmounted", async () => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init: RequestInit) => {
      if (init.signal) signals.push(init.signal);
      return new Promise<Response>(() => {});
    }),
  );
  const { unmount } = render(<VideoListView />);
  await waitFor(() => expect(signals.length).toBeGreaterThan(0));
  expect(signals[0].aborted).toBe(false);
  unmount();
  expect(signals[0].aborted).toBe(true);
});
