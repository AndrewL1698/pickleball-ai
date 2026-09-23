/**
 * The upload form: validation, the in-flight lock, and the error branches.
 *
 * Only `fetch` is stubbed. Everything else -- validation, focus, the live
 * region -- is the real component.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { UploadForm } from "@/components/UploadForm";
import { MAX_UPLOAD_BYTES } from "@/lib/files";
import { VIDEO_DETAIL, jsonResponse, videoFile } from "./fixtures";

// Async factory: `vi.mock` is hoisted above the imports, so the stub has to
// be pulled in when the factory runs rather than at module scope.
vi.mock("next/link", async () => (await import("./fixtures")).nextLinkMock());

function fileInput() {
  return screen.getByLabelText(/choose a video file/i) as HTMLInputElement;
}

describe("file selection", () => {
  it("accepts a valid video and names it in the hint", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    await user.upload(fileInput(), videoFile("match.mp4"));
    expect(screen.getByText(/Selected: match.mp4/)).toBeInTheDocument();
    expect(screen.queryByText(/not a supported video/i)).not.toBeInTheDocument();
  });

  it("rejects an unsupported extension and links the error to the input", async () => {
    // `applyAccept: false` mirrors what a real person can do: switch the picker
    // to "All Files", or drag the file in. Both bypass the `accept` hint, which
    // is exactly why client-side validation still has to run.
    const user = userEvent.setup({ applyAccept: false });
    render(<UploadForm />);
    await user.upload(fileInput(), videoFile("clip.avi"));

    const error = screen.getByText(/not a supported video/i);
    expect(error).toBeInTheDocument();

    // The error must be reachable from the input, not merely nearby.
    const input = fileInput();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")).toContain(error.id);
  });

  it("rejects a file over the size limit before contacting the server", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("huge.mp4", MAX_UPLOAD_BYTES + 1));
    expect(screen.getByText(/The limit is/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /upload video/i }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("asks for a file when submitted empty, and focuses the input", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    await user.click(screen.getByRole("button", { name: /upload video/i }));

    expect(screen.getByText(/Choose a video file to upload/)).toBeInTheDocument();
    expect(fileInput()).toHaveFocus();
  });

  it("keeps the file input in the accessibility tree rather than hiding it", () => {
    render(<UploadForm />);
    const input = fileInput();
    // sr-only, not `hidden`: a display:none input is unreachable by keyboard.
    expect(input).not.toHaveAttribute("hidden");
    expect(input.className).toContain("sr-only");
    expect(input).toHaveAttribute("accept", ".mp4,.mov,.m4v");
  });
});

describe("submitting", () => {
  it("uploads and offers a link to the new video", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(VIDEO_DETAIL, 201)));
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("match.mp4"));
    await user.click(screen.getByRole("button", { name: /upload video/i }));

    expect(await screen.findByRole("heading", { name: /upload complete/i })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /view processing status/i });
    expect(link).toHaveAttribute("href", `/videos/${VIDEO_DETAIL.id}`);
  });

  it("announces progress and completion through a live region", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(VIDEO_DETAIL, 201)));
    const user = userEvent.setup();
    const { container } = render(<UploadForm />);

    // The region exists, and is empty, before anything happens: a live region
    // mounted together with its first message is often not announced.
    const live = container.querySelector('[role="status"]');
    expect(live).toBeInTheDocument();
    expect(live).toHaveTextContent("");

    await user.upload(fileInput(), videoFile("match.mp4"));
    await user.click(screen.getByRole("button", { name: /upload video/i }));
    await waitFor(() => expect(live).toHaveTextContent(/upload complete/i));
  });

  it("does not submit twice while an upload is in flight", async () => {
    let resolve!: (value: Response) => void;
    const fetchMock = vi.fn(
      () => new Promise<Response>((r) => { resolve = r; }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("match.mp4"));
    const button = screen.getByRole("button", { name: /upload video/i });
    await user.click(button);

    const uploading = await screen.findByRole("button", { name: /uploading/i });
    expect(uploading).toHaveAttribute("aria-disabled", "true");
    await user.click(uploading);
    await user.click(uploading);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(jsonResponse(VIDEO_DETAIL, 201));
    await screen.findByRole("heading", { name: /upload complete/i });
  });

  it("shows the server's own message when it rejects the file", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { error_code: "unsupported_file_type", detail: "The file is not a readable mp4/mov video." },
          415,
        ),
      ),
    );
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("match.mp4"));
    await user.click(screen.getByRole("button", { name: /upload video/i }));

    expect(
      await screen.findByText(/not a readable mp4\/mov video/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /upload complete/i })).not.toBeInTheDocument();
  });

  it("does not claim the upload failed when only the queueing did", async () => {
    // The 503 means the video was stored and the rows exist -- telling someone
    // to upload it again would create a duplicate.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { error_code: "dependency_unavailable", detail: "The video was stored but could not be queued for processing." },
          503,
        ),
      ),
    );
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("match.mp4"));
    await user.click(screen.getByRole("button", { name: /upload video/i }));

    const message = await screen.findByText(/your video was saved/i);
    expect(message).toBeInTheDocument();
    expect(message.textContent).toMatch(/could not be queued/i);
  });

  it("reports an unreachable API in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const user = userEvent.setup();
    render(<UploadForm />);

    await user.upload(fileInput(), videoFile("match.mp4"));
    await user.click(screen.getByRole("button", { name: /upload video/i }));

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
  });
});

describe("drag and drop", () => {
  /** A drop event carrying files, which jsdom does not build on its own. */
  function dropEvent(files: File[]): Partial<DataTransfer> {
    return { files: files as unknown as FileList, dropEffect: "none", types: ["Files"] };
  }

  it("accepts a dropped video the same way as a chosen one", () => {
    render(<UploadForm />);
    const zone = screen.getByText(/choose a video file/i).closest("label")!;

    fireEvent.drop(zone, { dataTransfer: dropEvent([videoFile("dropped.mp4")]) });

    expect(screen.getByText(/Selected: dropped.mp4/)).toBeInTheDocument();
  });

  it("validates a dropped file, which bypasses the accept filter entirely", () => {
    render(<UploadForm />);
    const zone = screen.getByText(/choose a video file/i).closest("label")!;

    fireEvent.drop(zone, { dataTransfer: dropEvent([videoFile("notes.txt")]) });

    expect(screen.getByText(/not a supported video/i)).toBeInTheDocument();
  });

  it("cancels the browser's default drag handling", () => {
    // Without preventDefault on dragover the drop never fires and the browser
    // navigates away to open the file, losing the page.
    render(<UploadForm />);
    const zone = screen.getByText(/choose a video file/i).closest("label")!;

    const dragOver = new Event("dragover", { bubbles: true, cancelable: true });
    Object.defineProperty(dragOver, "dataTransfer", { value: { dropEffect: "none" } });
    fireEvent(zone, dragOver);

    expect(dragOver.defaultPrevented).toBe(true);
  });

  it("keeps the drag highlight until the pointer really leaves", () => {
    // Drag events fire for child elements too, so a plain boolean would
    // flicker as the pointer crosses the label's own contents.
    render(<UploadForm />);
    const zone = screen.getByText(/choose a video file/i).closest("label")!;
    const child = screen.getByText(/choose a video file/i);

    fireEvent.dragEnter(zone, { dataTransfer: dropEvent([]) });
    fireEvent.dragEnter(child, { dataTransfer: dropEvent([]) });
    fireEvent.dragLeave(child);
    expect(zone.className).toContain("border-accent");

    fireEvent.dragLeave(zone);
    expect(zone.className).not.toContain("border-accent");
  });
});
