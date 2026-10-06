/** Badges must not rely on colour alone. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchStatusBadge, StatusBadge } from "@/components/StatusBadge";
import { MATCH_STATUS_COPY, STATUS_COPY } from "@/lib/status";
import { JOB_STATUSES, MATCH_STATUSES } from "@/lib/types";

describe("StatusBadge", () => {
  it.each(JOB_STATUSES)("names the %s status in text, not only in colour", (status) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent(STATUS_COPY[status].label);
  });

  it("hides the decorative glyph from assistive technology", () => {
    render(<StatusBadge status="ready" />);
    const glyph = screen.getByTestId("status-badge").querySelector("[aria-hidden='true']");
    expect(glyph).toBeInTheDocument();
    // The word carries the meaning; the shape is a redundant cue for colour.
    expect(glyph?.textContent).toBe("✓");
  });

  it("gives each status a distinct glyph, so greyscale still distinguishes them", () => {
    const glyphs = JOB_STATUSES.map((status) => {
      const { container, unmount } = render(<StatusBadge status={status} />);
      const glyph = container.querySelector("[aria-hidden='true']")?.textContent;
      unmount();
      return glyph;
    });
    expect(new Set(glyphs).size).toBe(JOB_STATUSES.length);
  });
});

describe("MatchStatusBadge", () => {
  it.each(MATCH_STATUSES)("names the %s match status in text", (status) => {
    render(<MatchStatusBadge status={status} />);
    expect(screen.getByTestId("match-status-badge")).toHaveTextContent(
      MATCH_STATUS_COPY[status].label,
    );
  });

  it("gives each match status a distinct glyph", () => {
    const glyphs = MATCH_STATUSES.map((status) => {
      const { container, unmount } = render(<MatchStatusBadge status={status} />);
      const glyph = container.querySelector("[aria-hidden='true']")?.textContent;
      unmount();
      return glyph;
    });
    expect(new Set(glyphs).size).toBe(MATCH_STATUSES.length);
  });
});

describe("status copy", () => {
  it("has no match status that reads as a finished analysis", () => {
    for (const status of MATCH_STATUSES) {
      const { label, description } = MATCH_STATUS_COPY[status];
      expect(`${label} ${description}`).not.toMatch(
        /\bready\b|analysis complete|match analy[sz]ed|results/i,
      );
    }
  });

  it("describes a finished job as metadata, never as an analysed match", () => {
    expect(STATUS_COPY.ready.description).toMatch(/metadata only/i);
    expect(MATCH_STATUS_COPY.calibration_required.description).toMatch(/metadata has been extracted/i);
  });

  it("admits calibration is not available yet", () => {
    expect(MATCH_STATUS_COPY.calibration_required.description).toMatch(/not available/i);
  });

  it("never describes a ready job as an analysed match", () => {
    const ready = STATUS_COPY.ready;
    expect(`${ready.heading} ${ready.description} ${ready.announcement}`).not.toMatch(
      /analysis complete|match analysed|results ready/i,
    );
    expect(ready.description).toMatch(/no match analysis/i);
  });

  it("explains that a queued job may be waiting for a worker", () => {
    expect(STATUS_COPY.queued.description).toMatch(/worker/i);
  });
});
