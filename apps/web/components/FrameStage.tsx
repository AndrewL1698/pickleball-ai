"use client";

import { type KeyboardEvent, type PointerEvent, type MouseEvent, type ReactNode, useRef } from "react";
import { referenceFrameUrl } from "@/lib/api";
import type { Point } from "@/lib/homography";
import type { ReferenceFrame } from "@/lib/types";

/**
 * A calibration image with an SVG layer over it, both measured in image pixels.
 *
 * The SVG's viewBox is the image's own size, so what is drawn inside it lines up
 * however wide the page is, and `onPick` always reports a real image pixel.
 *
 * A plain `<img>`, not `next/image`: this has to be these exact pixels, from a
 * private API that the optimizer would have to be allowed to fetch from, and a
 * re-encode or resize would only make the landmarks harder to place.
 */
export function FrameStage({
  matchId,
  frame,
  label,
  instructions,
  onPick,
  onPointerAt,
  onPointerLeave,
  onPointerUp,
  onKeyDown,
  children,
}: {
  matchId: string;
  frame: ReferenceFrame;
  /** The image's alternative text. */
  label: string;
  /** How to use the stage, read out when it takes focus. */
  instructions: string;
  onPick?: (pixel: Point) => void;
  onPointerAt?: (pixel: Point) => void;
  onPointerLeave?: () => void;
  onPointerUp?: () => void;
  onKeyDown?: (event: KeyboardEvent<SVGSVGElement>) => void;
  children?: ReactNode;
}) {
  const svg = useRef<SVGSVGElement>(null);

  function pixelAt(event: MouseEvent | PointerEvent): Point | null {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0 || box.height === 0) return null;
    // Clamped: a drag can carry the pointer past the edge of the image, and the
    // API refuses a landmark outside it.
    const x = ((event.clientX - box.left) / box.width) * frame.width;
    const y = ((event.clientY - box.top) / box.height) * frame.height;
    return [clamp(x, 0, frame.width), clamp(y, 0, frame.height)];
  }

  return (
    <div className="relative overflow-hidden rounded-lg border border-border-subtle bg-surface">
      {/* eslint-disable-next-line @next/next/no-img-element -- see the docstring */}
      <img
        src={referenceFrameUrl(matchId, frame.id)}
        alt={label}
        width={frame.width}
        height={frame.height}
        draggable={false}
        className="block h-auto w-full select-none"
      />
      <svg
        ref={svg}
        data-testid="frame-stage"
        tabIndex={0}
        aria-label={instructions}
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        preserveAspectRatio="none"
        className="focus-ring absolute inset-0 h-full w-full cursor-crosshair touch-none"
        onClick={(event) => {
          const pixel = pixelAt(event);
          if (pixel) onPick?.(pixel);
        }}
        onPointerMove={(event) => {
          const pixel = pixelAt(event);
          if (pixel) onPointerAt?.(pixel);
        }}
        onPointerLeave={onPointerLeave}
        onPointerUp={onPointerUp}
        onKeyDown={onKeyDown}
      >
        {children}
      </svg>
    </div>
  );
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}
