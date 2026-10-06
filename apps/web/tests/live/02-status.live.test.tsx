/**
 * The real components polling the real API.
 *
 * Runs in jsdom, where GET requests work normally, and reads the match that
 * 01-upload just created. Together the two files cover what a browser check
 * would: an upload is accepted, the status page renders it, the status
 * changes, and a terminal state stops the polling.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, expect, it, vi } from "vitest";
import { MatchDetailView } from "@/components/MatchDetailView";
import { MatchListView } from "@/components/MatchListView";
import { API_BASE_URL, listMatches } from "@/lib/api";
import type { MatchSummary } from "@/lib/types";

// Async factory: `vi.mock` is hoisted above the imports, so the stub has to
// be pulled in when the factory runs rather than at module scope.
vi.mock("next/link", async () => (await import("../fixtures")).nextLinkMock());

let newest: MatchSummary;

beforeAll(async () => {
  const { matches } = await listMatches().catch(() => ({ matches: [] as MatchSummary[] }));
  if (matches.length === 0) {
    throw new Error(
      `No matches at ${API_BASE_URL}. Run the whole live suite so the upload test runs first.`,
    );
  }
  newest = matches[0];
});

it("renders the newest upload and follows its job to a terminal state", async () => {
  render(<MatchDetailView matchId={newest.id} pollIntervalMs={300} />);

  // Something real is on screen rather than a permanent skeleton.
  await waitFor(() => expect(screen.getByTestId("status-badge")).toBeInTheDocument(), {
    timeout: 15000,
  });

  // With the worker running this becomes `ready`. Going straight there from
  // `queued` without an observed `running` is normal: reading metadata
  // finishes in well under a second.
  await waitFor(
    () =>
      expect(
        screen.getByRole("heading", { name: /processing finished|processing failed/i }),
      ).toBeInTheDocument(),
    { timeout: 30000 },
  );

  expect(screen.getByText(/reads video metadata only/i)).toBeInTheDocument();
  // A finished job leaves the match waiting for calibration, never "ready".
  expect(screen.getByTestId("match-status-badge")).toHaveTextContent(
    /needs calibration|failed/i,
  );
  // Polling must stop once the answer cannot change.
  await waitFor(() => expect(screen.getByText(/checking has stopped/i)).toBeInTheDocument());
}, 60000);

it("lists that upload with a status badge", async () => {
  render(<MatchListView />);
  // findAllBy, not findBy: every run of this suite uploads another file with
  // the same name, so by the second run the name is not unique.
  const rows = await screen.findAllByRole(
    "link",
    { name: newest.name },
    { timeout: 15000 },
  );
  expect(rows.length).toBeGreaterThan(0);
  expect(screen.getAllByTestId("match-status-badge").length).toBeGreaterThanOrEqual(
    rows.length,
  );
}, 30000);
