/**
 * The polling page: the status lifecycle, and stopping cleanly.
 *
 * Real timers with a short interval rather than fake timers. Faking timers
 * around code that awaits promises means hand-pumping the microtask queue
 * between every tick, and the tests end up asserting on that machinery
 * instead of on the behaviour.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
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
  wait,
} from "./fixtures";
import type { Job } from "@/lib/types";

// Async factory: `vi.mock` is hoisted above the imports, so the stub has to
// be pulled in when the factory runs rather than at module scope.
vi.mock("next/link", async () => (await import("./fixtures")).nextLinkMock());

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
    await wait(120);
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
    await wait(150);
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

describe("headings and announcements in every state", () => {
  it("has a heading while loading, so the page is never heading-less", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("has a heading when the video cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error_code: "not_found", detail: "No such video." }, 404)),
    );
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    await screen.findByRole("alert");
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("names the video once it is loaded", async () => {
    respondWith([JOB_READY]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    expect(await screen.findByRole("heading", { level: 1, name: "demo.mp4" })).toBeInTheDocument();
  });

  it("treats an unparseable id as not found rather than a load failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error_code: "invalid_request", detail: "The request was not valid." }, 422),
      ),
    );
    render(<VideoStatusView videoId="not-a-uuid" pollIntervalMs={20} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not find that video/i);
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
    // Both halves matter: assistive technology registers a live region when it
    // is inserted and announces later changes, so the region has to exist
    // before there is anything to say, and it has to start empty.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { container } = render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);
    const live = container.querySelector('p[role="status"].sr-only');
    expect(live).toBeInTheDocument();
    expect(live).toHaveTextContent("");
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
    await wait(150);
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

  it("keeps the job on screen when the refresh itself fails", async () => {
    // A failed refresh is a transport problem. Replacing the whole page with
    // an error would throw away a job the server is perfectly happy with.
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        if (call === 1) return jsonResponse(withJob(JOB_READY));
        return jsonResponse({ error_code: "internal_error", detail: "Server blew up." }, 500);
      }),
    );
    const user = userEvent.setup();
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /file check complete/i });
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(call).toBeGreaterThan(1));
    expect(screen.getByRole("heading", { name: /file check complete/i })).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/ready/i);
    expect(screen.queryByText(/could not load this video/i)).not.toBeInTheDocument();
  }, 10000);

  it("marks the button busy while the refresh is in flight", async () => {
    let release!: (value: Response) => void;
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        call += 1;
        if (call === 1) return Promise.resolve(jsonResponse(withJob(JOB_READY)));
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }),
    );
    const user = userEvent.setup();
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /file check complete/i });
    const button = screen.getByRole("button", { name: /refresh/i });
    expect(button).toHaveAttribute("aria-disabled", "false");

    await user.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));

    release(jsonResponse(withJob(JOB_READY)));
    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "false"));
  }, 10000);

  it("does not disturb the button during background polling", async () => {
    // A poll nobody asked for must not flicker the control or announce itself.
    respondWith([JOB_QUEUED]);
    render(<VideoStatusView videoId={VIDEO_ID} pollIntervalMs={20} />);

    await screen.findByText(/waiting to start/i);
    const button = screen.getByRole("button", { name: /refresh/i });
    const seen = new Set<string | null>();
    // Eight samples over ~160ms spans several 20ms poll cycles, which is what
    // the assertion needs.
    for (let i = 0; i < 8; i += 1) {
      seen.add(button.getAttribute("aria-disabled"));
      // Inside act, because the polls landing during this wait update state.
      await act(async () => {
        await wait(20);
      });
    }
    expect([...seen]).toEqual(["false"]);
  }, 10000);
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
    await wait(200);
    expect(fetchMock).toHaveBeenCalledTimes(callsAtUnmount);
    expect(signals.at(-1)?.aborted).toBe(true);
  }, 10000);
});
