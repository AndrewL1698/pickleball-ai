import { HALF_LENGTH_FT, HALF_WIDTH_FT, KITCHEN_DEPTH_FT, PAINTED_LINES } from "@/lib/court";

/** Room around the lines, since players often stand outside them. */
const MARGIN_FT = 6;
const LEFT = -HALF_WIDTH_FT - MARGIN_FT;
const TOP = -HALF_LENGTH_FT - MARGIN_FT;
const WIDTH = (HALF_WIDTH_FT + MARGIN_FT) * 2;
const HEIGHT = (HALF_LENGTH_FT + MARGIN_FT) * 2;

export interface CourtMarker {
  id: string;
  /** Court feet. */
  x: number;
  y: number;
  label?: string;
  emphasis?: boolean;
}

/**
 * The court from above, in court coordinates: near half at the bottom, right
 * as seen from the near baseline facing the net. SVG's Y runs down the page,
 * so court Y is negated once, here, and a caller only ever deals in feet.
 *
 * A marker beyond the margin is left off rather than pinned to the edge, where
 * it would claim a position nobody measured.
 */
export function TopDownCourt({
  markers = [],
  label,
}: {
  markers?: CourtMarker[];
  label: string;
}) {
  const visible = markers.filter(
    (m) => Math.abs(m.x) <= HALF_WIDTH_FT + MARGIN_FT && Math.abs(m.y) <= HALF_LENGTH_FT + MARGIN_FT,
  );
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`${LEFT} ${TOP} ${WIDTH} ${HEIGHT}`}
      className="h-auto w-full max-w-72 rounded-lg border border-border-subtle bg-surface text-muted"
    >
      <rect
        x={-HALF_WIDTH_FT}
        y={-KITCHEN_DEPTH_FT}
        width={HALF_WIDTH_FT * 2}
        height={KITCHEN_DEPTH_FT * 2}
        fill="currentColor"
        opacity={0.12}
      />
      {PAINTED_LINES.map(([[x0, y0], [x1, y1]]) => (
        <line
          key={`${x0},${y0},${x1},${y1}`}
          x1={x0}
          y1={-y0}
          x2={x1}
          y2={-y1}
          stroke="currentColor"
          strokeWidth={0.3}
        />
      ))}
      <line
        data-testid="court-net"
        x1={-HALF_WIDTH_FT - 1}
        y1={0}
        x2={HALF_WIDTH_FT + 1}
        y2={0}
        stroke="currentColor"
        strokeWidth={0.3}
        strokeDasharray="1 0.6"
      />
      <text x={0} y={HALF_LENGTH_FT + 3.5} textAnchor="middle" fontSize={2} fill="currentColor">
        Near (camera side)
      </text>
      <text x={0} y={-HALF_LENGTH_FT - 2} textAnchor="middle" fontSize={2} fill="currentColor">
        Far
      </text>
      {visible.map((m) => (
        <g key={m.id} data-testid={`court-marker-${m.id}`}>
          <circle
            cx={m.x}
            cy={-m.y}
            r={m.emphasis ? 1 : 0.6}
            fill={m.emphasis ? "var(--accent)" : "currentColor"}
            stroke="var(--background)"
            strokeWidth={0.2}
          />
          {m.label ? (
            <text x={m.x + 1.2} y={-m.y + 0.6} fontSize={1.8} fill="var(--foreground)">
              {m.label}
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  );
}
