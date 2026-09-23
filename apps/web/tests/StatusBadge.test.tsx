/** Badges must not rely on colour alone. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/StatusBadge";
import { STATUS_COPY } from "@/lib/status";
import { JOB_STATUSES } from "@/lib/types";

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

describe("status copy", () => {
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
