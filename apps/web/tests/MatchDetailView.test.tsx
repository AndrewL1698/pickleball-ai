/**
 * The polling page: the status lifecycle, and stopping cleanly.
 *
 * Real timers with a short interval rather than fake timers. Faking timers
 * around code that awaits promises means hand-pumping the microtask queue
 * between every tick, and the tests end up asserting on that machinery
 * instead of on the behaviour.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MatchDetailView } from "@/components/MatchDetailView";
import {
  JOB_FAILED,
  JOB_QUEUED,
  JOB_READY,
  JOB_RUNNING,
  MATCH_DETAIL,
  VIDEO,
  VIDEO_METADATA,
  jsonResponse,
  wait,
} from "./fixtures";
import type { Job, MatchStatus } from "@/lib/types";

// Async factory: `vi.mock` is hoisted above the imports, so the stub has to
// be pulled in when the factory runs rather than at module scope.
vi.mock("next/link", async () => (await import("./fixtures")).nextLinkMock());

const MATCH_ID = MATCH_DETAIL.id;

/** The match status the server derives from each job status. */
const MATCH_STATUS_FOR: Record<Job["status"], MatchStatus> = {
  queued: "uploaded",
  running: "processing",
  ready: "calibration_required",
  failed: "failed",
};

/** The match as the server returns it once its latest job is `job`: a job
 * that finished today has saved its metadata, and nothing active means the
 * extraction action is available only when metadata is missing. */
function withJob(job: Job) {
  const metadata = job.status === "ready" ? VIDEO_METADATA : null;
  const active = job.status === "queued" || job.status === "running";
  return {
    ...MATCH_DETAIL,
    status: MATCH_STATUS_FOR[job.status],
    video: { ...VIDEO, metadata },
    latest_job: job,
    jobs: [job],
    can_extract_metadata: metadata === null && !active,
  };
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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    expect(await screen.findByText(/waiting to start/i)).toBeInTheDocument();
    expect(await screen.findByText(/reading video metadata/i)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /processing finished/i })).toBeInTheDocument();

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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("heading", { name: /processing finished/i })).toBeInTheDocument();
  }, 10000);

  it("does not poll at all when the job is already finished", async () => {
    const fetchMock = respondWith([JOB_READY]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /processing finished/i });
    await wait(150);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a failed job's sanitized message and code", async () => {
    respondWith([JOB_FAILED]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("heading", { name: /processing failed/i })).toBeInTheDocument();
    expect(screen.getByText("The video could not be read.")).toBeInTheDocument();
    expect(screen.getByText(/unreadable_video/)).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/failed/i);
  });
});

describe("headings and announcements in every state", () => {
  it("has a heading while loading, so the page is never heading-less", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("has a heading when the match cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error_code: "not_found", detail: "No such match." }, 404)),
    );
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("alert");
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("names the match once it is loaded", async () => {
    respondWith([JOB_READY]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    expect(await screen.findByRole("heading", { level: 1, name: "demo" })).toBeInTheDocument();
    // The original filename is shown as metadata, not as the title.
    expect(screen.getByText("demo.mp4")).toBeInTheDocument();
  });

  it("treats an unparseable id as not found rather than a load failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error_code: "invalid_request", detail: "The request was not valid." }, 422),
      ),
    );
    render(<MatchDetailView matchId="not-a-uuid" pollIntervalMs={20} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not find that match/i);
  });
});

describe("honesty about what was analysed", () => {
  it("never claims the match was analysed", async () => {
    respondWith([JOB_READY]);
    const { container } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing finished/i });

    expect(screen.getByText(/reads video metadata only/i)).toBeInTheDocument();
    expect(screen.getByText(/No match analysis has been performed/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/analysis complete/i);
    expect(container.textContent).not.toMatch(/view results/i);
  });

  it("says the match needs calibration, and that calibration is not available", async () => {
    respondWith([JOB_READY]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing finished/i });
    expect(screen.getByTestId("match-status-badge")).toHaveTextContent(/needs calibration/i);
    expect(screen.getByText(/calibration is not available in this build/i)).toBeInTheDocument();
  });

  it("shows a failed match alongside its failed job", async () => {
    respondWith([JOB_FAILED]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing failed/i });
    expect(screen.getByTestId("match-status-badge")).toHaveTextContent(/failed/i);
  });

  it("follows the match status as its job moves", async () => {
    respondWith([JOB_QUEUED, JOB_RUNNING, JOB_READY]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await waitFor(() =>
      expect(screen.getByTestId("match-status-badge")).toHaveTextContent(/needs calibration/i),
    );
  }, 10000);

  it("no longer claims metadata is not extracted once it has been", async () => {
    respondWith([JOB_READY]);
    const { container } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing finished/i });
    expect(container.textContent).not.toMatch(/not extracted yet/i);
  });
});

describe("announcements", () => {
  it("keeps a live region mounted and empty from the first render", () => {
    // Both halves matter: assistive technology registers a live region when it
    // is inserted and announces later changes, so the region has to exist
    // before there is anything to say, and it has to start empty.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { container } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    const live = container.querySelector('p[role="status"].sr-only');
    expect(live).toBeInTheDocument();
    expect(live).toHaveTextContent("");
  });

  it("announces the status in words, without anything that ticks", async () => {
    respondWith([JOB_QUEUED, JOB_READY]);
    const { container } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await waitFor(() => {
      const live = container.querySelector('p[role="status"].sr-only');
      expect(live?.textContent).toMatch(/processing finished/i);
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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await screen.findByText(/waiting to start/i);
    expect(await screen.findByText(/could not check for updates/i)).toBeInTheDocument();
    // The job itself is still queued: a transport failure is not a job failure.
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/queued/i);
    expect(screen.queryByRole("heading", { name: /processing failed/i })).not.toBeInTheDocument();
  }, 10000);

  it("stops and reports when the match does not exist", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ error_code: "not_found", detail: "No such match." }, 404),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not find that match/i);
    expect(screen.getByRole("link", { name: /back to matches/i })).toHaveAttribute(
      "href",
      "/matches",
    );
    // A 404 will not fix itself, so it must not be retried forever.
    await wait(150);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("manual refresh", () => {
  it("fetches again when asked", async () => {
    const fetchMock = respondWith([JOB_READY]);
    const user = userEvent.setup();
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /processing finished/i });
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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /processing finished/i });
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(call).toBeGreaterThan(1));
    expect(screen.getByRole("heading", { name: /processing finished/i })).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent(/ready/i);
    expect(screen.queryByText(/could not load this match/i)).not.toBeInTheDocument();
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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await screen.findByRole("heading", { name: /processing finished/i });
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
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

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

    const { unmount } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
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

describe("video metadata", () => {
  function metadataSection() {
    return screen.getByRole("region", { name: /video metadata/i });
  }

  it("shows every decoded field, in display orientation, with the average labelled", async () => {
    respondWith([JOB_READY]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing finished/i });

    const section = metadataSection();
    const value = (label: RegExp) =>
      within(section).getByText(label).nextElementSibling?.textContent;
    expect(value(/resolution \(as displayed\)/i)).toBe("1080 × 1920");
    expect(value(/^rotation$/i)).toMatch(/90°/);
    expect(value(/duration \(estimate\)/i)).toBe("12:34");
    expect(value(/^frame rate$/i)).toBe("29.97 fps average");
    expect(value(/^frames$/i)).toBe((22603).toLocaleString());
    expect(value(/^codec$/i)).toBe("hvc1");
    expect(within(section).getByText(/frame rate is an average/i)).toBeInTheDocument();
    // Metadata complete is not analysis complete.
    expect(screen.getByTestId("match-status-badge")).toHaveTextContent(/needs calibration/i);
  });

  it("says when a video does not name its codec instead of leaving a blank", async () => {
    const match = withJob(JOB_READY);
    const noCodec = { ...match, video: { ...VIDEO, metadata: { ...VIDEO_METADATA, codec: null } } };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(noCodec)));
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing finished/i });
    expect(within(metadataSection()).getByText(/not named by the file/i)).toBeInTheDocument();
  });

  it("says metadata is being extracted while the job is active, with no action", async () => {
    respondWith([JOB_RUNNING]);
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /reading video metadata/i });
    expect(within(metadataSection()).getByText(/being extracted/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /extract metadata|try again/i })).toBeNull();
  });

  it("offers a retry after a failure and never shows zeros", async () => {
    respondWith([JOB_FAILED]);
    const { container } = render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing failed/i });
    expect(within(metadataSection()).getByText(/no metadata was saved/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/0 × 0|0\.00 fps/);
  });

  it("offers extraction for a match processed before extraction existed", async () => {
    // A backfilled Phase 1 match: its job finished, but nothing was decoded.
    const legacy = {
      ...withJob(JOB_READY),
      status: "uploaded" as const,
      video: { ...VIDEO, metadata: null },
      can_extract_metadata: true,
    };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(legacy)));
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    expect(
      await screen.findByText(/processed before metadata extraction was added/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /extract metadata/i })).toBeInTheDocument();
  });

  it("hides the action whenever the server says it would be refused", async () => {
    const refused = { ...withJob(JOB_FAILED), can_extract_metadata: false };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(refused)));
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    await screen.findByRole("heading", { name: /processing failed/i });
    expect(screen.queryByRole("button", { name: /try again|extract metadata/i })).toBeNull();
  });

  it("starts a job, then follows it to completion", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    let phase: "failed" | "queued" | "ready" = "failed";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        calls.push({ url, method });
        if (method === "POST") {
          phase = "queued";
          return jsonResponse(withJob(JOB_QUEUED), 202);
        }
        const current = phase === "failed" ? JOB_FAILED : phase === "queued" ? JOB_QUEUED : JOB_READY;
        if (phase === "queued") phase = "ready";
        return jsonResponse(withJob(current));
      }),
    );
    const user = userEvent.setup();
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);

    await user.click(await screen.findByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("heading", { name: /processing finished/i }, { timeout: 3000 }))
      .toBeInTheDocument();
    expect(within(metadataSection()).getByText("1080 × 1920")).toBeInTheDocument();

    const post = calls.filter((c) => c.method === "POST");
    expect(post).toHaveLength(1);
    expect(post[0].url).toMatch(new RegExp(`/api/matches/${MATCH_ID}/metadata-jobs$`));
  }, 10000);

  it("does not send a second request while the first is in flight", async () => {
    let release!: (value: Response) => void;
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          posts.push(url);
          return new Promise<Response>((resolve) => { release = resolve; });
        }
        return Promise.resolve(jsonResponse(withJob(JOB_FAILED)));
      }),
    );
    const user = userEvent.setup();
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    const button = await screen.findByRole("button", { name: /try again/i });
    await user.click(button);
    const busy = await screen.findByRole("button", { name: /starting/i });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    await user.click(busy);
    expect(posts).toHaveLength(1);
    release(jsonResponse(withJob(JOB_QUEUED), 202));
  });

  it("shows the server's refusal next to the button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? jsonResponse(
              {
                error_code: "conflict",
                detail: "A processing job for this match is already queued or running.",
              },
              409,
            )
          : jsonResponse(withJob(JOB_FAILED)),
      ),
    );
    const user = userEvent.setup();
    render(<MatchDetailView matchId={MATCH_ID} pollIntervalMs={20} />);
    const button = await screen.findByRole("button", { name: /try again/i });
    await user.click(button);
    const message = await screen.findByText(/already queued or running/i);
    expect(button.getAttribute("aria-describedby")).toBe(message.id);
  });
});
