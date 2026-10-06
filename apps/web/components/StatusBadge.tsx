import type { JobStatus, MatchStatus } from "@/lib/types";
import { MATCH_STATUS_COPY, STATUS_COPY } from "@/lib/status";

/**
 * Colour is never the only signal: every badge carries its word, and a glyph
 * whose shape differs per status, so the meaning survives greyscale and
 * colour blindness. Text colours are the -800/-200 ends of each ramp, which
 * clear 4.5:1 on their own tint in both schemes.
 */
const STYLES: Record<JobStatus, { glyph: string; className: string }> = {
  queued: {
    glyph: "○",
    className:
      "bg-slate-100 text-slate-800 ring-slate-600/25 dark:bg-slate-400/15 dark:text-slate-200 dark:ring-slate-400/30",
  },
  running: {
    glyph: "◐",
    className:
      "bg-blue-50 text-blue-800 ring-blue-700/25 dark:bg-blue-400/15 dark:text-blue-100 dark:ring-blue-400/30",
  },
  ready: {
    glyph: "✓",
    className:
      "bg-green-50 text-green-800 ring-green-700/25 dark:bg-green-400/15 dark:text-green-100 dark:ring-green-400/30",
  },
  failed: {
    glyph: "✕",
    className:
      "bg-red-50 text-red-800 ring-red-700/25 dark:bg-red-400/15 dark:text-red-100 dark:ring-red-400/30",
  },
};

const MATCH_STYLES: Record<MatchStatus, { glyph: string; className: string }> = {
  uploaded: STYLES.queued,
  processing: STYLES.running,
  // Amber, not green: this is a step still to do, not a finished result.
  calibration_required: {
    glyph: "◇",
    className:
      "bg-amber-50 text-amber-800 ring-amber-700/25 dark:bg-amber-400/15 dark:text-amber-100 dark:ring-amber-400/30",
  },
  court_ready: STYLES.ready,
  failed: STYLES.failed,
};

/** A job's status. */
export function StatusBadge({ status }: { status: JobStatus }) {
  return <Badge testId="status-badge" {...STYLES[status]} label={STATUS_COPY[status].label} />;
}

/** A match's status: the summary shown in lists and page headers. */
export function MatchStatusBadge({ status }: { status: MatchStatus }) {
  return (
    <Badge
      testId="match-status-badge"
      {...MATCH_STYLES[status]}
      label={MATCH_STATUS_COPY[status].label}
    />
  );
}

function Badge({
  testId,
  glyph,
  className,
  label,
}: {
  testId: string;
  glyph: string;
  className: string;
  label: string;
}) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${className}`}
    >
      <span aria-hidden="true" className="leading-none">
        {glyph}
      </span>
      {label}
    </span>
  );
}
