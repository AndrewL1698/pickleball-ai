/** The four states the list has to handle. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { MatchListView } from "@/components/MatchListView";
import { MATCH_SUMMARY, jsonResponse } from "./fixtures";

// Async factory: `vi.mock` is hoisted above the imports, so the stub has to
// be pulled in when the factory runs rather than at module scope.
vi.mock("next/link", async () => (await import("./fixtures")).nextLinkMock());

it("shows a named loading state while the first request is in flight", async () => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  render(<MatchListView />);
  // A spinner with no accessible name says nothing; the text is the point.
  expect(await screen.findByText(/loading your matches/i)).toBeInTheDocument();
});

it("shows an inviting empty state, not an error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ matches: [], count: 0 })));
  render(<MatchListView />);

  expect(await screen.findByRole("heading", { name: /no matches yet/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /upload a match/i })).toHaveAttribute("href", "/");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("lists matches by name, with their file, date and match status", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ matches: [MATCH_SUMMARY], count: 1 })),
  );
  render(<MatchListView />);

  const link = await screen.findByRole("link", { name: "demo" });
  expect(link).toHaveAttribute("href", `/matches/${MATCH_SUMMARY.id}`);
  // A row must be a real link, so it can be tabbed to and opened in a new tab.
  expect(link.tagName).toBe("A");
  expect(screen.getByText("demo.mp4")).toBeInTheDocument();
  expect(screen.getByTestId("match-status-badge")).toHaveTextContent(/uploaded/i);
});

it("renders each match's own status", async () => {
  const second = {
    ...MATCH_SUMMARY,
    id: "other",
    name: "b",
    status: "calibration_required" as const,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ matches: [MATCH_SUMMARY, second], count: 2 })),
  );
  render(<MatchListView />);

  await screen.findByRole("link", { name: "demo" });
  const badges = screen.getAllByTestId("match-status-badge");
  expect(badges[0]).toHaveTextContent(/uploaded/i);
  expect(badges[1]).toHaveTextContent(/needs calibration/i);
});

it("never labels a processed match as ready or analysed", async () => {
  const processed = { ...MATCH_SUMMARY, status: "calibration_required" as const };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ matches: [processed], count: 1 })));
  const { container } = render(<MatchListView />);
  await screen.findByRole("link", { name: "demo" });
  expect(container.textContent).not.toMatch(/\bready\b|analy[sz]ed|analysis complete/i);
});

it("shows a match that has lost its video without pretending it has one", async () => {
  const orphan = { ...MATCH_SUMMARY, video: null, latest_job: null };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ matches: [orphan], count: 1 })));
  render(<MatchListView />);

  expect(await screen.findByRole("link", { name: "demo" })).toBeInTheDocument();
  expect(screen.getByText(/no video/i)).toBeInTheDocument();
});

it("announces the outcome once the list has settled", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ matches: [MATCH_SUMMARY], count: 1 })),
  );
  const { container } = render(<MatchListView />);
  await screen.findByRole("link", { name: "demo" });
  expect(container.querySelector('p[role="status"].sr-only')).toHaveTextContent(
    "1 match loaded.",
  );
});

it("announces a load failure and offers a retry that actually refetches", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      jsonResponse({ error_code: "internal_error", detail: "Something went wrong." }, 500),
    )
    .mockResolvedValueOnce(jsonResponse({ matches: [MATCH_SUMMARY], count: 1 }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MatchListView />);

  // Unprompted failures announce themselves rather than stealing focus.
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/could not load your matches/i);

  await user.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByRole("link", { name: "demo" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("tells the user plainly when the API is not running", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
  render(<MatchListView />);
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
  const { unmount } = render(<MatchListView />);
  await waitFor(() => expect(signals.length).toBeGreaterThan(0));
  expect(signals[0].aborted).toBe(false);
  unmount();
  expect(signals[0].aborted).toBe(true);
});
