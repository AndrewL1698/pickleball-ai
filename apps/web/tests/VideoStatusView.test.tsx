/**
 * The polling page: the status lifecycle, and stopping cleanly.
 *
 * Real timers with a short interval rather than fake timers. Faking timers
 * around code that awaits promises means hand-pumping the microtask queue
 * between every tick, and the tests end up asserting on that machinery
 * instead of on the behaviour.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { VideoStatusView } from "@/components/VideoStatusView";
import {
  JOB_FAILED,
  JOB_QUEUED,
  JOB_READY,
  JOB_RUNNING,
  VIDEO_DETAIL,
  jsonResponse,
} from "./fixtures";
import type { Job } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

const VIDEO_ID = VIDEO_DETAIL.id;

function withJob(job: Job) {
  return { ...VIDEO_DETAIL, latest_job: job, jobs: [job] };
}

/** Answers each poll with the next job state, repeating the last one. */
function respondWith(sequence: Job[]) {
  let call = 0;
  const fetchMock = vi.fn(async () => {
    const job = sequence[Math.min(call, sequence.length - 1)];
    call += 1;
    return jsonResponse(withJob(job));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("status lifecycle", () => {
  it("follows a job from queued through running to ready, then stops polling", async () => {
    const fetchMock = respondWith([JOB_QUEUED, JOB_RUNNING, JOB_READY]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    expect(await screen.findByText(/waiting to start/i)).toBeInTheDocument();
    expect(await screen.findByText(/checking the file/i)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /file check complete/i })).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByText(/checking has stopped/i)).toBeInTheDocument(),
    );

    // Terminal means terminal: no further requests once it is ready.
    const settled = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(fetchMock).toHaveBeenCalledTimes(settled);
  }, 10000);

  it("handles queued going straight to ready, which is what usually happens", async () => {
    // The placeholder processor finishes in milliseconds, so `running` is
    // frequently never observed between two polls.
    respondWith([JOB_QUEUED, JOB_READY]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("heading", { name: /file check complete/i })).toBeInTheDocument();
  }, 10000);

  it("does not poll at all when the job is already finished", async () => {
    const fetchMock = respondWith([JOB_READY]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /file check complete/i });
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a failed job's sanitized message and code", async () => {
    respondWith([JOB_FAILED]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("heading", { name: /processing failed/i })).toBeInTheDocument();
    expect(screen.getByText("The video could not be read.")).toBeInTheDocument();
    expect(screen.getByText(/unreadable_video/)).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/failed/i);
  });
});

describe("honesty about the placeholder", () => {
  it("never claims the match was analysed", async () => {
    respondWith([JOB_READY]);
    const { container } = render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /file check complete/i });

    expect(screen.getByText(/does not analyse video yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No match analysis has been performed/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/analysis complete/i);
    expect(container.textContent).not.toMatch(/view results/i);
  });

  it("says metadata is missing rather than showing empty fields", async () => {
    respondWith([JOB_READY]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /file check complete/i });
    expect(
      screen.getByText(/duration, resolution and frame rate are not extracted yet/i),
    ).toBeInTheDocument();
  });
});

describe("announcements", () => {
  it("keeps a live region mounted and empty from the first render", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { container } = render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    const live = container.querySelector('[role="status"]');
    expect(live).toBeInTheDocument();
  });

  it("announces the status in words, without anything that ticks", async () => {
    respondWith([JOB_QUEUED, JOB_READY]);
    const { container } = render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await waitFor(() => {
      const live = container.querySelector('p[role="status"].sr-only');
      expect(live?.textContent).toMatch(/file check finished/i);
    });
    // No timestamp or counter, so an unchanged status re-announces nothing.
    const live = container.querySelector('p[role="status"].sr-only');
    expect(live?.textContent).not.toMatch(/\d{2}:\d{2}/);
  }, 10000);
});

describe("failures while polling", () => {
  it("keeps the last known status when a refresh fails", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        if (call === 1) return jsonResponse(withJob(JOB_QUEUED));
        return jsonResponse({ error_code: "internal_error", detail: "Server blew up." }, 500);
      }),
    );
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByText(/waiting to start/i);
    expect(await screen.findByText(/could not check for updates/i)).toBeInTheDocument();
    // The job itself is still queued: a transport failure is not a job failure.
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/queued/i);
    expect(screen.queryByRole("heading", { name: /processing failed/i })).not.toBeInTheDocument();
  }, 10000);

  it("stops and reports when the video does not exist", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ error_code: "not_found", detail: "No such video." }, 404),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not find that video/i);
    // A 404 will not fix itself, so it must not be retried forever.
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("manual refresh", () => {
  it("fetches again when asked", async () => {
    const fetchMock = respondWith([JOB_READY]);
    const user = userEvent.setup();
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /file check complete/i });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});

describe("cleanup", () => {
  it("stops polling and aborts in flight work when unmounted", async () => {
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      if (init.signal) signals.push(init.signal);
      return jsonResponse(withJob(JOB_QUEUED));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    await screen.findByText(/waiting to start/i);
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));

    const callsAtUnmount = fetchMock.mock.calls.length;
    unmount();

    // The queued job would otherwise poll forever; nothing may follow unmount.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(fetchMock).toHaveBeenCalledTimes(callsAtUnmount);
    expect(signals.at(-1)?.aborted).toBe(true);
  }, 10000);
});
