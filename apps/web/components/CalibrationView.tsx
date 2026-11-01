"use client";

import Link from "next/link";
import { useCallback, useId, useRef, useState } from "react";
import { ApiError, createCalibration, getMatch, referenceFrameUrl } from "@/lib/api";
import { LANDMARKS, describeCourtPosition } from "@/lib/court";
import { formatMediaDuration } from "@/lib/format";
import { type Point, projectToCourt } from "@/lib/homography";
import {
  LANDMARK_NAMES,
  type CourtCalibration,
  type LandmarkName,
  type MatchDetail,
  type ReferenceFrame,
} from "@/lib/types";
import { usePolledResource } from "@/hooks/usePolledResource";
import { FrameStage } from "./FrameStage";
import { Notice } from "./Notice";
import { Spinner } from "./Spinner";
import { type CourtMarker, TopDownCourt } from "./TopDownCourt";

/** The fewest landmarks the API will fit. It decides; this only guides the button. */
const MINIMUM_LANDMARKS = 4;

/** Image pixels per screen pixel in the magnifier. */
const LOUPE_ZOOM = 4;
const LOUPE_SIZE = 192;

/** What an arrow key moves a landmark by, in image pixels. */
const NUDGE = 1;
const LARGE_NUDGE = 10;

const LINE_COLOR = "#22d3ee";
const POINT_COLOR = "#fbbf24";
const ACTIVE_COLOR = "#f43f5e";
const OUTLINE_COLOR = "#0f172a";

/**
 * Calibrating a match's court: place landmarks on a still from its video, save,
 * and check the result against the image and a top-down court.
 *
 * The match is read once. Nothing here changes without this page asking, and a
 * background poll could only overwrite what the person is in the middle of doing.
 */
export function CalibrationView({ matchId }: { matchId: string }) {
  const fetcher = useCallback((signal: AbortSignal) => getMatch(matchId, signal), [matchId]);
  const { data, error, isLoading } = usePolledResource<MatchDetail>(fetcher, {});
  const [saved, setSaved] = useState<MatchDetail | null>(null);
  const match = saved ?? data;

  return (
    // Wider than the app's reading column: the image is the whole job here, and
    // every pixel of width makes a landmark easier to place.
    <div className="relative left-1/2 w-[min(76rem,calc(100vw-2rem))] -translate-x-1/2 space-y-6">
      <div>
        <Link href={`/matches/${matchId}`} className="focus-ring text-sm text-muted underline">
          Back to match
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight break-words">
          Calibrate the court{match ? `: ${match.name}` : ""}
        </h1>
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-muted">
          <Spinner />
          Loading match…
        </p>
      ) : match === null ? (
        <Notice
          tone="error"
          role="alert"
          title={error?.status === 404 ? "We could not find that match." : "We could not load this match."}
          code={error?.code}
        >
          <p>{error?.message ?? "Something went wrong."}</p>
        </Notice>
      ) : match.reference_frames.length === 0 || !match.can_calibrate ? (
        <Notice tone="info" title="This match cannot be calibrated yet.">
          <p>{notReadyReason(match)} The match page shows what is happening with it.</p>
        </Notice>
      ) : (
        <>
          <CalibrationEditor match={match} onSaved={setSaved} />
          {match.calibration ? (
            <CalibrationCheck
              key={match.calibration.id}
              match={match}
              calibration={match.calibration}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

function notReadyReason(match: MatchDetail): string {
  const job = match.latest_job;
  if (job?.status === "queued" || job?.status === "running") {
    return "A processing job is still running, and calibration images appear when it finishes.";
  }
  if (job?.status === "failed") return "Its latest processing job failed.";
  return "It has no calibration images.";
}

function frameLabel(frame: ReferenceFrame): string {
  return frame.position_seconds === null
    ? `Background, players removed (${frame.source_frame_count} frames)`
    : `About ${formatMediaDuration(frame.position_seconds)} into the video`;
}

function FramePicker({
  name,
  legend,
  frames,
  selected,
  onSelect,
}: {
  /** Groups the radio buttons; each picker on the page needs its own. */
  name: string;
  legend: string;
  frames: ReferenceFrame[];
  selected: ReferenceFrame;
  onSelect: (frameId: string) => void;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {frames.map((option) => (
          <label key={option.id} className="flex min-h-8 items-center gap-2">
            <input
              type="radio"
              name={name}
              className="focus-ring"
              checked={option.id === selected.id}
              onChange={() => onSelect(option.id)}
            />
            {frameLabel(option)}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function CalibrationEditor({
  match,
  onSaved,
}: {
  match: MatchDetail;
  onSaved: (match: MatchDetail) => void;
}) {
  const previous = match.calibration;
  const [frameId, setFrameId] = useState(
    previous?.reference_frame_id ?? match.reference_frames[0].id,
  );
  const frame = match.reference_frames.find((f) => f.id === frameId) ?? match.reference_frames[0];

  const [points, setPoints] = useState<Partial<Record<LandmarkName, Point>>>(() =>
    previous ? (previous.image_points as Partial<Record<LandmarkName, Point>>) : {},
  );
  const [hidden, setHidden] = useState<ReadonlySet<LandmarkName>>(
    () =>
      new Set(previous ? LANDMARK_NAMES.filter((name) => !(name in previous.image_points)) : []),
  );
  const [active, setActive] = useState<LandmarkName | null>(previous ? null : LANDMARK_NAMES[0]);
  const [pointer, setPointer] = useState<Point | null>(null);
  const dragging = useRef<LandmarkName | null>(null);

  const problemId = useId();
  const inFlight = useRef(false);
  const [isSaving, setIsSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const placedCount = LANDMARK_NAMES.filter((name) => points[name]).length;

  /** The next landmark still needing an answer, after this one. */
  function nextAfter(
    name: LandmarkName,
    placed: Partial<Record<LandmarkName, Point>>,
    notVisible: ReadonlySet<LandmarkName>,
  ): LandmarkName | null {
    const start = LANDMARK_NAMES.indexOf(name);
    const ordered = [...LANDMARK_NAMES.slice(start + 1), ...LANDMARK_NAMES.slice(0, start)];
    return ordered.find((other) => !placed[other] && !notVisible.has(other)) ?? null;
  }

  function place(name: LandmarkName, pixel: Point) {
    setPoints((current) => ({ ...current, [name]: pixel }));
    setHidden((current) => {
      if (!current.has(name)) return current;
      const next = new Set(current);
      next.delete(name);
      return next;
    });
    setProblem(null);
  }

  function markNotVisible(name: LandmarkName) {
    const nextPoints = { ...points };
    delete nextPoints[name];
    const nextHidden = new Set(hidden).add(name);
    setPoints(nextPoints);
    setHidden(nextHidden);
    setProblem(null);
    if (active === name) setActive(nextAfter(name, nextPoints, nextHidden));
  }

  /**
   * Handled around both the stage and the landmark list, so that a landmark just
   * chosen from the list can be nudged straight away. Enter stays on the stage,
   * where it would otherwise do nothing: on a list button it has to press it.
   */
  function handleKey(event: React.KeyboardEvent<HTMLDivElement>) {
    if (active === null) return;
    const current = points[active];
    if (event.key === "Enter" && !current && event.target instanceof SVGSVGElement) {
      event.preventDefault();
      place(active, [frame.width / 2, frame.height / 2]);
      return;
    }
    const step = event.shiftKey ? LARGE_NUDGE : NUDGE;
    const moves: Record<string, Point> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const move = moves[event.key];
    if (!move || !current) return;
    event.preventDefault();
    // The magnifier follows the landmark being moved, not a pointer left resting.
    setPointer(null);
    place(active, [
      Math.min(frame.width, Math.max(0, current[0] + move[0])),
      Math.min(frame.height, Math.max(0, current[1] + move[1])),
    ]);
  }

  async function save() {
    if (inFlight.current) return;
    if (placedCount < MINIMUM_LANDMARKS) {
      setProblem(`Place at least ${MINIMUM_LANDMARKS} landmarks. ${placedCount} placed so far.`);
      return;
    }
    inFlight.current = true;
    setIsSaving(true);
    setProblem(null);
    try {
      onSaved(
        await createCalibration(match.id, {
          reference_frame_id: frame.id,
          image_points: points,
        }),
      );
    } catch (cause) {
      // The API's refusals are fixed sentences, written to be shown.
      setProblem(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
    } finally {
      inFlight.current = false;
      setIsSaving(false);
    }
  }

  const activePoint = active ? (points[active] ?? null) : null;
  const radius = frame.width / 170;
  const courtMarkers: CourtMarker[] = active
    ? [{ id: active, x: LANDMARKS[active].x, y: LANDMARKS[active].y, emphasis: true }]
    : [];

  return (
    <section aria-labelledby="place-heading" className="space-y-4">
      <div>
        <h2 id="place-heading" className="text-lg font-semibold">
          Place the landmarks
        </h2>
        <p className="mt-1 text-sm text-muted">
          Select a landmark, then click where those painted lines cross. Near means nearer the
          camera. Left and right are as seen from the near baseline, facing the net. Place as many
          as you can see, across both halves of the court, and mark the rest as not visible. Never
          use the net or its posts: they are above the ground. The far baseline is small in the
          image, so a pixel there is several inches on the court: use the arrow keys to move a
          selected landmark one pixel at a time, with the magnifier to judge it.
        </p>
      </div>

      {match.reference_frames.length > 1 ? (
        <div>
          <FramePicker
            name="place-frame"
            legend="Image to place them on"
            frames={match.reference_frames}
            selected={frame}
            onSelect={setFrameId}
          />
          <p className="mt-1 text-sm text-muted">
            If the lines in the background look doubled or smeared, the camera moved during the
            video. Use a single frame instead, and expect positions to be wrong after the move.
          </p>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]" onKeyDown={handleKey}>
        <div className="min-w-0 space-y-4">
          <FrameStage
            matchId={match.id}
            frame={frame}
            label={`The court: ${frameLabel(frame)}`}
            instructions="The court image. Click to place the selected landmark. With the keyboard, press Enter to place it in the middle of the image, then use the arrow keys to move it, with Shift for larger steps."
            onPick={(pixel) => {
              if (active === null) return;
              place(active, pixel);
              setActive(nextAfter(active, { ...points, [active]: pixel }, hidden));
            }}
            onPointerAt={(pixel) => {
              setPointer(pixel);
              if (dragging.current) place(dragging.current, pixel);
            }}
            onPointerLeave={() => setPointer(null)}
            onPointerUp={() => {
              dragging.current = null;
            }}
          >
            {LANDMARK_NAMES.map((name, index) => {
              const point = points[name];
              if (!point) return null;
              return (
                <g
                  key={name}
                  data-testid={`landmark-point-${name}`}
                  className="cursor-grab"
                  onPointerDown={(event) => {
                    // Not captured: moves keep reaching the stage, which knows
                    // how to turn them into pixels.
                    event.stopPropagation();
                    dragging.current = name;
                    setActive(name);
                  }}
                  // A press on a point selects it and must not also place the
                  // selected landmark underneath the pointer.
                  onClick={(event) => event.stopPropagation()}
                >
                  <circle
                    cx={point[0]}
                    cy={point[1]}
                    r={radius}
                    fill={name === active ? ACTIVE_COLOR : POINT_COLOR}
                    fillOpacity={0.45}
                    stroke={OUTLINE_COLOR}
                    strokeWidth={radius / 4}
                  />
                  <text
                    x={point[0] + radius * 1.4}
                    y={point[1] - radius * 1.2}
                    fontSize={radius * 2.2}
                    fontWeight={700}
                    fill={name === active ? ACTIVE_COLOR : POINT_COLOR}
                    stroke={OUTLINE_COLOR}
                    strokeWidth={radius / 6}
                    paintOrder="stroke"
                  >
                    {index + 1}
                  </text>
                </g>
              );
            })}
          </FrameStage>
          <ol className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
            {LANDMARK_NAMES.map((name, index) => (
              <li key={name} className="flex items-center justify-between gap-2">
                <button
                  type="button"
                  aria-pressed={name === active}
                  onClick={() => setActive(name)}
                  className={`focus-ring min-h-9 min-w-0 flex-1 rounded-md px-2 text-left aria-pressed:bg-surface aria-pressed:font-semibold ${
                    name === active ? "ring-1 ring-accent" : ""
                  }`}
                >
                  {index + 1}. {LANDMARKS[name].label}
                  <span className="block text-xs font-normal text-muted">
                    {points[name]
                      ? `Placed at ${Math.round(points[name][0])}, ${Math.round(points[name][1])}`
                      : hidden.has(name)
                        ? "Not visible"
                        : "Not placed"}
                  </span>
                </button>
                {hidden.has(name) ? null : (
                  <button
                    type="button"
                    aria-label={`Mark ${LANDMARKS[name].label} as not visible`}
                    onClick={() => markNotVisible(name)}
                    className="focus-ring min-h-9 shrink-0 rounded-md border border-border-subtle px-2 text-xs"
                  >
                    Not visible
                  </button>
                )}
              </li>
            ))}
          </ol>
        </div>

        <div className="space-y-4">
          <Loupe matchId={match.id} frame={frame} center={pointer ?? activePoint} />
          <TopDownCourt
            markers={courtMarkers}
            label={
              active
                ? `Where ${LANDMARKS[active].label} is on the court.`
                : "The court from above, with no landmark selected."
            }
          />
        </div>
      </div>


      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          aria-disabled={isSaving}
          aria-describedby={problem ? problemId : undefined}
          onClick={() => void save()}
          className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent px-4 font-medium text-background aria-disabled:cursor-default aria-disabled:opacity-60"
        >
          {isSaving ? <Spinner /> : null}
          {isSaving ? "Saving…" : previous ? "Save a new calibration" : "Save calibration"}
        </button>
        <p className="text-sm text-muted">
          {placedCount} of {LANDMARK_NAMES.length} placed.
          {previous ? " The earlier calibration is kept, and this one replaces it in use." : ""}
        </p>
      </div>
      {problem ? (
        <p id={problemId} role="alert" className="text-sm font-medium text-red-700 dark:text-red-300">
          <span aria-hidden="true">⚠ </span>
          {problem}
        </p>
      ) : null}
    </section>
  );
}

/**
 * A magnified view around the pointer or the selected landmark. A painted line
 * is a few pixels wide on a still scaled to fit the page, so the crossing cannot
 * be judged at the size the image is shown.
 */
function Loupe({
  matchId,
  frame,
  center,
}: {
  matchId: string;
  frame: ReferenceFrame;
  center: Point | null;
}) {
  const half = LOUPE_SIZE / 2;
  return (
    <div
      aria-hidden="true"
      data-testid="loupe"
      className="relative overflow-hidden rounded-lg border border-border-subtle bg-surface"
      style={{
        width: LOUPE_SIZE,
        height: LOUPE_SIZE,
        ...(center
          ? {
              backgroundImage: `url("${referenceFrameUrl(matchId, frame.id)}")`,
              backgroundRepeat: "no-repeat",
              backgroundSize: `${frame.width * LOUPE_ZOOM}px ${frame.height * LOUPE_ZOOM}px`,
              backgroundPosition: `${half - center[0] * LOUPE_ZOOM}px ${half - center[1] * LOUPE_ZOOM}px`,
              imageRendering: "pixelated",
            }
          : {}),
      }}
    >
      {center ? (
        <>
          <span className="absolute top-0 left-1/2 h-full w-px bg-rose-500" />
          <span className="absolute top-1/2 left-0 h-px w-full bg-rose-500" />
        </>
      ) : (
        <span className="flex h-full items-center justify-center p-4 text-center text-xs text-muted">
          Point at the image to magnify it.
        </span>
      )}
    </div>
  );
}

/**
 * The saved calibration, in the terms a person can judge it by: how well the
 * clicks agree, whether the projected lines sit on the painted ones, and whether
 * a clicked point lands where it should on the top-down court.
 */
function CalibrationCheck({
  match,
  calibration,
}: {
  match: MatchDetail;
  calibration: CourtCalibration;
}) {
  const [frameId, setFrameId] = useState(calibration.reference_frame_id);
  const frame =
    match.reference_frames.find((f) => f.id === frameId) ?? match.reference_frames[0];
  const [clicked, setClicked] = useState<Point | null>(null);

  // Any landmark is a pixel known to be on the ground.
  const onCourt = Object.values(calibration.image_points)[0];
  const position = clicked ? projectToCourt(calibration.homography, clicked, onCourt) : null;
  const landmarkNames = LANDMARK_NAMES.filter((name) => name in calibration.reprojection_error_ft);
  const strokeWidth = frame.width / 640;

  return (
    <section aria-labelledby="check-heading" className="space-y-4">
      <div>
        <h2 id="check-heading" className="text-lg font-semibold">
          Check the calibration
        </h2>
        <p className="mt-1 text-sm text-muted">
          The blue lines are where this calibration puts the court. They should sit on the painted
          lines. If they do not, fix the landmarks above and save again.
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-muted">Landmarks used</dt>
          <dd className="font-medium">{landmarkNames.length}</dd>
        </div>
        <div>
          <dt className="text-muted">Average error</dt>
          <dd className="font-medium">{calibration.mean_reprojection_error_ft.toFixed(2)} ft</dd>
        </div>
        <div>
          <dt className="text-muted">Largest error</dt>
          <dd className="font-medium">{calibration.max_reprojection_error_ft.toFixed(2)} ft</dd>
        </div>
      </dl>
      <p className="text-sm text-muted">
        The error is how well your landmarks agree with each other. It is not a measurement of how
        accurate a player&rsquo;s position will be.
      </p>

      {calibration.warnings.length > 0 ? (
        <Notice tone="error" role="status" title="Review this calibration.">
          <ul className="space-y-1">
            {calibration.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      {match.reference_frames.length > 1 ? (
        <FramePicker
          name="check-frame"
          legend="Image to check on (a single frame has players in it, to click on their feet)"
          frames={match.reference_frames}
          selected={frame}
          onSelect={setFrameId}
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="min-w-0 space-y-4">
          <FrameStage
            matchId={match.id}
            frame={frame}
            label={`The court with the calibrated lines drawn over it: ${frameLabel(frame)}`}
            instructions="The court image with the calibrated lines. Click a point on the ground to see where it lands on the top-down court."
            onPick={setClicked}
          >
            {Object.entries(calibration.projected_lines).map(([name, [start, end]]) => (
              <line
                key={name}
                data-testid={`projected-line-${name}`}
                x1={start[0]}
                y1={start[1]}
                x2={end[0]}
                y2={end[1]}
                stroke={LINE_COLOR}
                strokeWidth={strokeWidth}
                strokeDasharray={name === "net" ? `${strokeWidth * 4} ${strokeWidth * 3}` : undefined}
              />
            ))}
            {clicked ? (
              <circle
                data-testid="clicked-point"
                cx={clicked[0]}
                cy={clicked[1]}
                r={strokeWidth * 3}
                fill={ACTIVE_COLOR}
                stroke={OUTLINE_COLOR}
                strokeWidth={strokeWidth}
              />
            ) : null}
          </FrameStage>
          <details className="text-sm">
            <summary className="focus-ring cursor-pointer font-medium">Error for each landmark</summary>
            <table className="mt-2 w-full max-w-md text-left">
              <thead>
                <tr className="text-muted">
                  <th scope="col" className="py-1 font-normal">
                    Landmark
                  </th>
                  <th scope="col" className="py-1 text-right font-normal">
                    Error (ft)
                  </th>
                </tr>
              </thead>
              <tbody>
                {landmarkNames.map((name) => (
                  <tr key={name} className="border-t border-border-subtle">
                    <td className="py-1">{LANDMARKS[name].label}</td>
                    <td className="py-1 text-right tabular-nums">
                      {calibration.reprojection_error_ft[name]?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
        <div className="space-y-3">
          <TopDownCourt
            markers={position ? [{ id: "clicked", x: position[0], y: position[1], emphasis: true }] : []}
            label={
              position
                ? `The clicked point on the court, at ${position[0].toFixed(1)}, ${position[1].toFixed(1)} feet.`
                : "The court from above. Click the image to place a point on it."
            }
          />
          <div role="status" className="text-sm">
            {clicked === null ? (
              <p className="text-muted">Click a point on the ground in the image.</p>
            ) : position === null ? (
              <p>That point is above the horizon, so it is not on the court plane.</p>
            ) : (
              <>
                <p className="font-medium">
                  X {position[0].toFixed(1)} ft, Y {position[1].toFixed(1)} ft
                </p>
                <p>{describeCourtPosition(position[0], position[1])}</p>
              </>
            )}
          </div>
          <p className="text-xs text-muted">
            Only correct for a point on the ground, such as where a player&rsquo;s foot touches it.
            A point in the air, such as a ball or a player&rsquo;s head, lands in the wrong place.
          </p>
        </div>
      </div>

    </section>
  );
}
