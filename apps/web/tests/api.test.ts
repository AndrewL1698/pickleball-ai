/**
 * The API client's failure handling.
 *
 * Only the network boundary is mocked: `fetch` is stubbed, everything else is
 * the real code.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJob, listVideos, uploadVideo } from "@/lib/api";
import { JOB_READY, VIDEO_DETAIL, jsonResponse, videoFile } from "./fixtures";

function mockFetch(implementation: (...args: never[]) => Promise<Response>) {
  const fetchMock = vi.fn(implementation);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("listVideos", () => {
  it("returns the parsed body", async () => {
    mockFetch(async () => jsonResponse({ videos: [], count: 0 }));
    await expect(listVideos()).resolves.toEqual({ videos: [], count: 0 });
  });

  it("calls the configured base URL and asks for no caching", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ videos: [], count: 0 }));
    await listVideos();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/videos$/);
    expect(init.cache).toBe("no-store");
  });
});

describe("error handling", () => {
  it("uses the server's own sentence for a 404", async () => {
    mockFetch(async () =>
      jsonResponse({ error_code: "not_found", detail: "No such job." }, 404),
    );
    const error = await getJob("missing").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("No such job.");
    expect((error as ApiError).code).toBe("not_found");
    expect((error as ApiError).status).toBe(404);
  });

  it("does not retry a 404, but does retry a 500", async () => {
    mockFetch(async () => jsonResponse({ error_code: "not_found", detail: "No such job." }, 404));
    await expect(getJob("x")).rejects.toMatchObject({ isRetryable: false });

    mockFetch(async () =>
      jsonResponse({ error_code: "internal_error", detail: "Something went wrong." }, 500),
    );
    await expect(getJob("x")).rejects.toMatchObject({ isRetryable: true });
  });

  it("falls back to a readable sentence when the body is not the error shape", async () => {
    mockFetch(async () => new Response("Invalid host header", { status: 400 }));
    const error = (await getJob("x").catch((e: unknown) => e)) as ApiError;
    expect(error.message).toBe("The request was rejected.");
    expect(error.code).toBe("unexpected_response");
  });

  it("reports an unreachable server rather than a cryptic fetch failure", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const error = (await listVideos().catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("unreachable");
    expect(error.message).toMatch(/Is it running\?/);
  });

  it("rejects a 200 whose body is not JSON", async () => {
    mockFetch(async () => new Response("<html>oops</html>", { status: 200 }));
    const error = (await getJob("x").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("malformed_response");
  });

  it("lets an abort through untouched, so callers can ignore their own cancellation", async () => {
    mockFetch(async () => {
      throw new DOMException("aborted", "AbortError");
    });
    await expect(getJob("x")).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("uploadVideo", () => {
  beforeEach(() => {
    mockFetch(async () => jsonResponse(VIDEO_DETAIL, 201));
  });

  it("posts the file under the field name the API expects", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(VIDEO_DETAIL, 201));
    await uploadVideo(videoFile("match.mp4"));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("file")).toBeInstanceOf(File);
    expect((body.get("file") as File).name).toBe("match.mp4");
  });

  it("never sets Content-Type, which would break the multipart boundary", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(VIDEO_DETAIL, 201));
    await uploadVideo(videoFile());
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toBeUndefined();
  });

  it("surfaces the server's rejection message for an unsupported file", async () => {
    mockFetch(async () =>
      jsonResponse(
        { error_code: "unsupported_file_type", detail: "Only .m4v, .mov, .mp4 files are accepted." },
        415,
      ),
    );
    await expect(uploadVideo(videoFile("x.mp4"))).rejects.toThrow(
      "Only .m4v, .mov, .mp4 files are accepted.",
    );
  });

  it("returns the created video with its job", async () => {
    const created = await uploadVideo(videoFile());
    expect(created.jobs).toHaveLength(1);
    expect(created.latest_job?.status).toBe("queued");
  });
});

describe("getJob", () => {
  it("parses a terminal job", async () => {
    mockFetch(async () => jsonResponse(JOB_READY));
    await expect(getJob(JOB_READY.id)).resolves.toMatchObject({ status: "ready", progress: 1 });
  });
});
