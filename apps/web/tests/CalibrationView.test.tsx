/**
 * The calibration page. `fetch` is the only thing stubbed, so the placing,
 * validation and rendering are the real code.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CalibrationView } from "@/components/CalibrationView";
import { TopDownCourt } from "@/components/TopDownCourt";
import { LANDMARK_NAMES } from "@/lib/types";
import type { MatchDetail } from "@/lib/types";
import {
  CALIBRATABLE_MATCH,
  CALIBRATED_MATCH,
  COURT_CALIBRATION,
  JOB_FAILED,
  JOB_RUNNING,
  MATCH_DETAIL,
  REFERENCE_FRAME,
  jsonResponse,
} from "./fixtures";

vi.mock("next/link", async () => (await import("./fixtures")).nextLinkMock());

const MATCH_ID = MATCH_DETAIL.id;

/** Answer the GET with `match`, and any POST with what `post` says. */
function serve(match: MatchDetail, post?: () => Response) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === "POST" ? (post?.() ?? jsonResponse(CALIBRATED_MATCH, 201)) : jsonResponse(match),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function postBodies(fetchMock: ReturnType<typeof serve>) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([url, init]) => ({ url, init, body: JSON.parse(init?.body as string) }));
}

/** Give every stage a known on-screen size: half the image's pixels each way. */
function stubStageSize(stage: Element) {
  stage.getBoundingClientRect = () =>
    ({ left: 100, top: 50, width: 960, height: 540, right: 1060, bottom: 590, x: 100, y: 50 }) as DOMRect;
}

async function openEditor() {
  render(<CalibrationView matchId={MATCH_ID} />);
  await screen.findByRole("heading", { name: /place the landmarks/i });
  const stage = screen.getAllByTestId("frame-stage")[0];
  stubStageSize(stage);
  return stage;
}

/** A click on the stage at these image pixels. */
function clickPixel(stage: Element, x: number, y: number) {
  fireEvent.click(stage, { clientX: 100 + x / 2, clientY: 50 + y / 2 });
}

function placeFirstLandmarks(stage: Element, count: number) {
  for (let i = 0; i < count; i += 1) clickPixel(stage, 100 + i * 200, 100 + (i % 3) * 300);
}

describe("reaching the page", () => {
  it("says so when the match cannot be found", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error_code: "not_found", detail: "No such match." }, 404)),
    );
    render(<CalibrationView matchId={MATCH_ID} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not find that match/i);
  });

  it.each([
    [JOB_RUNNING, /still running/i],
    [JOB_FAILED, /processing job failed/i],
  ])("explains why a match cannot be calibrated, and offers nothing to click", async (job, reason) => {
    serve({ ...MATCH_DETAIL, latest_job: job, jobs: [job] });
    render(<CalibrationView matchId={MATCH_ID} />);
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.queryByTestId("frame-stage")).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
  });
});

describe("placing landmarks", () => {
  it("turns a click into an image pixel, however the image is scaled, and moves on", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    clickPixel(stage, 400, 300);
    expect(screen.getByText("Placed at 400, 300")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^2\. near baseline, center/i, pressed: true }),
    ).toBeInTheDocument();
    expect(screen.getByTestId(`landmark-point-${LANDMARK_NAMES[0]}`)).toBeInTheDocument();
  });

  it("shows the selected landmark on the top-down court", async () => {
    serve(CALIBRATABLE_MATCH);
    await openEditor();
    const marker = screen.getByTestId(`court-marker-${LANDMARK_NAMES[0]}`);
    expect(marker.querySelector("circle")).toHaveAttribute("cx", "-10");
    // Court Y is negated for the page: the near baseline is at the bottom.
    expect(marker.querySelector("circle")).toHaveAttribute("cy", "22");
  });

  it("skips a landmark that is not visible, and never sends it", async () => {
    const fetchMock = serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    await userEvent.click(
      screen.getByRole("button", { name: /mark near baseline, left corner as not visible/i }),
    );
    expect(screen.getByRole("button", { name: /^1\. near baseline, left corner/i })).toHaveTextContent(
      /not visible/i,
    );
    expect(
      screen.getByRole("button", { name: /^2\. near baseline, center/i, pressed: true }),
    ).toBeInTheDocument();

    placeFirstLandmarks(stage, 4);
    await userEvent.click(screen.getByRole("button", { name: /save calibration/i }));
    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    const points = postBodies(fetchMock)[0].body.image_points;
    expect(Object.keys(points)).toHaveLength(4);
    expect(points).not.toHaveProperty("near_left_baseline_corner");
  });

  it("places a landmark again when it is reselected", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    clickPixel(stage, 400, 300);
    await userEvent.click(screen.getByRole("button", { name: /^1\. near baseline, left corner/i }));
    clickPixel(stage, 500, 310);
    expect(screen.getByText("Placed at 500, 310")).toBeInTheDocument();
    expect(screen.queryByText("Placed at 400, 300")).toBeNull();
  });

  it("drags a placed landmark, and pressing one does not also place another", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    clickPixel(stage, 400, 300);

    const point = screen.getByTestId(`landmark-point-${LANDMARK_NAMES[0]}`);
    fireEvent.pointerDown(point);
    fireEvent.pointerMove(stage, { clientX: 100 + 300, clientY: 50 + 200 });
    fireEvent.pointerUp(stage);
    fireEvent.click(point);
    expect(screen.getByText("Placed at 600, 400")).toBeInTheDocument();
    expect(screen.getAllByText(/^Placed at/)).toHaveLength(1);

    // The drag is over, so moving no longer moves it.
    fireEvent.pointerMove(stage, { clientX: 100 + 10, clientY: 50 + 10 });
    expect(screen.getByText("Placed at 600, 400")).toBeInTheDocument();
  });

  it("keeps a landmark dragged past the edge inside the image", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    clickPixel(stage, 400, 300);
    fireEvent.pointerDown(screen.getByTestId(`landmark-point-${LANDMARK_NAMES[0]}`));
    fireEvent.pointerMove(stage, { clientX: 5000, clientY: -5000 });
    expect(screen.getByText(`Placed at ${REFERENCE_FRAME.width}, 0`)).toBeInTheDocument();
  });

  it("can be done from the keyboard", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    fireEvent.keyDown(stage, { key: "Enter" });
    expect(screen.getByText("Placed at 960, 540")).toBeInTheDocument();
    fireEvent.keyDown(stage, { key: "ArrowRight" });
    expect(screen.getByText("Placed at 961, 540")).toBeInTheDocument();
    fireEvent.keyDown(stage, { key: "ArrowUp", shiftKey: true });
    expect(screen.getByText("Placed at 961, 530")).toBeInTheDocument();
  });

  it("nudges a landmark chosen from the list without moving focus to the image", async () => {
    serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    clickPixel(stage, 400, 300);
    const row = screen.getByRole("button", { name: /^1\. near baseline, left corner/i });
    await userEvent.click(row);
    expect(row).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByText("Placed at 400, 301")).toBeInTheDocument();
  });

  it("leaves Enter on a list button to press it, not to place a landmark", async () => {
    serve(CALIBRATABLE_MATCH);
    await openEditor();
    const row = screen.getByRole("button", { name: /^2\. near baseline, center/i });
    row.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByText(/^Placed at/)).toBeNull();
    expect(row).toHaveAttribute("aria-pressed", "true");
  });

  it("offers each image, and uses the chosen one's id", async () => {
    const fetchMock = serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    expect(screen.getByRole("radio", { name: /background, players removed/i })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /3:09 into the video/i }));
    placeFirstLandmarks(stage, 4);
    await userEvent.click(screen.getByRole("button", { name: /save calibration/i }));
    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    expect(postBodies(fetchMock)[0].body.reference_frame_id).toBe(
      CALIBRATABLE_MATCH.reference_frames[1].id,
    );
  });

  it("shows the real image, served by the API, at its own size", async () => {
    serve(CALIBRATABLE_MATCH);
    await openEditor();
    const image = screen.getByRole("img", { name: /the court: background/i });
    expect(image.getAttribute("src")).toMatch(
      new RegExp(`/api/matches/${MATCH_ID}/reference-frames/${REFERENCE_FRAME.id}$`),
    );
    expect(image).toHaveAttribute("width", String(REFERENCE_FRAME.width));
  });
});

describe("saving", () => {
  it("refuses too few landmarks before asking the server", async () => {
    const fetchMock = serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    placeFirstLandmarks(stage, 3);
    await userEvent.click(screen.getByRole("button", { name: /save calibration/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/at least 4 landmarks\. 3 placed/i);
    expect(postBodies(fetchMock)).toHaveLength(0);
  });

  it("sends JSON to the calibrations route, and shows the result", async () => {
    const fetchMock = serve(CALIBRATABLE_MATCH);
    const stage = await openEditor();
    placeFirstLandmarks(stage, 5);
    await userEvent.click(screen.getByRole("button", { name: /save calibration/i }));

    expect(await screen.findByRole("heading", { name: /check the calibration/i })).toBeInTheDocument();
    const [post] = postBodies(fetchMock);
    expect(post.url).toMatch(new RegExp(`/api/matches/${MATCH_ID}/calibrations$`));
    expect(post.init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(post.body.reference_frame_id).toBe(REFERENCE_FRAME.id);
    expect(post.body.image_points.near_left_baseline_corner).toEqual([100, 100]);
    expect(screen.getByText("0.23 ft")).toBeInTheDocument();
  });

  it("shows the server's reason when it refuses, and keeps what was placed", async () => {
    serve(CALIBRATABLE_MATCH, () =>
      jsonResponse({ error_code: "invalid_request", detail: "Left and right look swapped." }, 422),
    );
    const stage = await openEditor();
    placeFirstLandmarks(stage, 4);
    await userEvent.click(screen.getByRole("button", { name: /save calibration/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Left and right look swapped.");
    expect(screen.getAllByText(/^Placed at/)).toHaveLength(4);
    expect(screen.queryByRole("heading", { name: /check the calibration/i })).toBeNull();
  });
});

describe("checking a saved calibration", () => {
  async function openCheck(match: MatchDetail = CALIBRATED_MATCH) {
    serve(match);
    render(<CalibrationView matchId={MATCH_ID} />);
    const heading = await screen.findByRole("heading", { name: /check the calibration/i });
    const section = heading.closest("section") as HTMLElement;
    const stage = within(section).getByTestId("frame-stage");
    stubStageSize(stage);
    return { section, stage };
  }

  it("starts from the saved landmarks, with the unplaced ones not visible", async () => {
    serve(CALIBRATED_MATCH);
    render(<CalibrationView matchId={MATCH_ID} />);
    await screen.findByRole("heading", { name: /check the calibration/i });
    expect(screen.getAllByText(/^Placed at/)).toHaveLength(5);
    expect(screen.getByRole("button", { name: /^2\. near baseline, center/i })).toHaveTextContent(
      /not visible/i,
    );
    expect(screen.getByRole("button", { name: /save a new calibration/i })).toBeInTheDocument();
  });

  it("draws the lines the API projected", async () => {
    const { section } = await openCheck();
    const line = within(section).getByTestId("projected-line-near_baseline");
    expect(line).toHaveAttribute("x1", "400");
    expect(line).toHaveAttribute("x2", "1520");
  });

  it("reports the errors, and that they do not measure position accuracy", async () => {
    const { section } = await openCheck();
    expect(within(section).getByText("0.23 ft")).toBeInTheDocument();
    expect(within(section).getByText("0.40 ft")).toBeInTheDocument();
    expect(within(section).getByText(/not a measurement of how accurate/i)).toBeInTheDocument();
  });

  it("shows nothing to review when the API has no warnings", async () => {
    await openCheck();
    expect(screen.queryByText(/review this calibration/i)).toBeNull();
  });

  it("shows the API's warnings", async () => {
    await openCheck({
      ...CALIBRATED_MATCH,
      calibration: { ...COURT_CALIBRATION, warnings: ["The landmarks disagree."] },
    });
    expect(screen.getByText(/review this calibration/i)).toBeInTheDocument();
    expect(screen.getByText("The landmarks disagree.")).toBeInTheDocument();
  });

  it("puts a clicked point on the top-down court, in words too", async () => {
    const { section, stage } = await openCheck();
    // The fixture's homography: X = x - 960, Y = 500 - y, w = 1.
    clickPixel(stage, 963, 512);
    expect(within(section).getByRole("status")).toHaveTextContent("X 3.0 ft, Y -12.0 ft");
    expect(within(section).getByRole("status")).toHaveTextContent(
      "Near half, 5.0 ft behind the kitchen line.",
    );
    const marker = within(section).getByTestId("court-marker-clicked").querySelector("circle");
    expect(marker).toHaveAttribute("cx", "3");
    expect(marker).toHaveAttribute("cy", "12");
  });

  it("says a point above the horizon is not on the court, and marks nothing", async () => {
    const { section, stage } = await openCheck({
      ...CALIBRATED_MATCH,
      calibration: {
        ...COURT_CALIBRATION,
        homography: [
          [1, 0, -960],
          [0, 1, -500],
          [0, 0.01, -1],
        ],
      },
    });
    clickPixel(stage, 960, 50);
    expect(within(section).getByRole("status")).toHaveTextContent(/above the horizon/i);
    expect(within(section).queryByTestId("court-marker-clicked")).toBeNull();
  });

  it("can check on a single frame, which has players in it", async () => {
    const { section } = await openCheck();
    expect(within(section).getByRole("img", { name: /background/i })).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("radio", { name: /3:09 into the video/i }));
    expect(within(section).getByRole("img", { name: /3:09 into the video/i })).toBeInTheDocument();
    // The calibration is the camera's, so the same lines still apply.
    expect(within(section).getByTestId("projected-line-near_baseline")).toBeInTheDocument();
  });

  it("warns that the projection is for points on the ground", async () => {
    const { section } = await openCheck();
    expect(within(section).getByText(/only correct for a point on the ground/i)).toBeInTheDocument();
  });

  it("replaces the check when a new calibration comes back", async () => {
    const newer = {
      ...CALIBRATED_MATCH,
      calibration: {
        ...COURT_CALIBRATION,
        id: "bbbb1111-2222-4333-8444-555566667777",
        mean_reprojection_error_ft: 0.11,
      },
    };
    serve(CALIBRATED_MATCH, () => jsonResponse(newer, 201));
    render(<CalibrationView matchId={MATCH_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: /save a new calibration/i }));
    expect(await screen.findByText("0.11 ft")).toBeInTheDocument();
    expect(screen.queryByText("0.23 ft")).toBeNull();
  });
});

describe("TopDownCourt", () => {
  it("leaves off a marker beyond its margin rather than pinning it to the edge", () => {
    render(
      <TopDownCourt
        label="court"
        markers={[
          { id: "in", x: 12, y: -25 },
          { id: "out", x: 40, y: 0 },
        ]}
      />,
    );
    expect(screen.getByTestId("court-marker-in")).toBeInTheDocument();
    expect(screen.queryByTestId("court-marker-out")).toBeNull();
  });

  it("is labelled for assistive technology", () => {
    render(<TopDownCourt label="The court from above." />);
    expect(screen.getByRole("img", { name: "The court from above." })).toBeInTheDocument();
  });
});
