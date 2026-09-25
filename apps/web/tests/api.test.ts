/**
 * The API client's failure handling.
 *
 * Only the network boundary is mocked: `fetch` is stubbed, everything else is
 * the real code.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createMatch, getJob, getMatch, listMatches } from "@/lib/api";
import { JOB_READY, MATCH_DETAIL, jsonResponse, videoFile } from "./fixtures";

function mockFetch(implementation: (...args: never[]) => Promise<Response>) {
  const fetchMock = vi.fn(implementation);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("listMatches", () => {
  it("returns the parsed body", async () => {
    mockFetch(async () => jsonResponse({ matches: [], count: 0 }));
    await expect(listMatches()).resolves.toEqual({ matches: [], count: 0 });
  });

  it("calls the configured base URL and asks for no caching", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ matches: [], count: 0 }));
    await listMatches();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/matches$/);
    expect(init.cache).toBe("no-store");
  });
});

describe("getMatch", () => {
  it("asks for one match by id, escaped", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(MATCH_DETAIL));
    await expect(getMatch("a/b")).resolves.toMatchObject({ id: MATCH_DETAIL.id });
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toMatch(/\/api\/matches\/a%2Fb$/);
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
    const error = (await listMatches().catch((e: unknown) => e)) as ApiError;
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

describe("createMatch", () => {
  beforeEach(() => {
    mockFetch(async () => jsonResponse(MATCH_DETAIL, 201));
  });

  it("posts the file to the matches collection under the field name the API expects", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(MATCH_DETAIL, 201));
    await createMatch(videoFile("match.mp4"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/matches$/);
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("file")).toBeInstanceOf(File);
    expect((body.get("file") as File).name).toBe("match.mp4");
  });

  it("never sets Content-Type, which would break the multipart boundary", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(MATCH_DETAIL, 201));
    await createMatch(videoFile());
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
    await expect(createMatch(videoFile("x.mp4"))).rejects.toThrow(
      "Only .m4v, .mov, .mp4 files are accepted.",
    );
  });

  it("returns the created match with its video and job", async () => {
    const created = await createMatch(videoFile());
    expect(created.video?.original_filename).toBe("demo.mp4");
    expect(created.jobs).toHaveLength(1);
    expect(created.latest_job?.status).toBe("queued");
    expect(created.latest_job?.match_id).toBe(created.id);
  });
});

describe("getJob", () => {
  it("parses a terminal job", async () => {
    mockFetch(async () => jsonResponse(JOB_READY));
    await expect(getJob(JOB_READY.id)).resolves.toMatchObject({ status: "ready", progress: 1 });
  });
});
